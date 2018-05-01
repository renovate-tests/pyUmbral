import os

from cryptography.hazmat.backends.openssl import backend
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes

from umbral import openssl
from umbral.config import default_curve, default_params
from umbral.utils import get_curve_keysize_bytes


class CurveBN(object):
    """
    Represents an OpenSSL Bignum modulo the order of a curve. Some of these
    operations will only work with prime numbers
    """

    def __init__(self, bignum, curve_nid, group, order):

        if curve_nid:
            on_curve = openssl._bn_is_on_curve(bignum, curve_nid)
            if not on_curve:
                raise ValueError("The provided BIGNUM is not on the provided curve.")

        self.bignum = bignum
        self.curve_nid = curve_nid
        self.group = group
        self.order = order

    @classmethod
    def get_size(cls, curve: ec.EllipticCurve=None):
        """
        Returns the size (in bytes) of a CurveBN given the curve.
        If no curve is provided, it uses the default.
        """
        curve = curve if curve is not None else default_curve()
        return get_curve_keysize_bytes(curve)

    @classmethod
    def gen_rand(cls, curve: ec.EllipticCurve=None):
        """
        Returns a CurveBN object with a cryptographically secure OpenSSL BIGNUM
        based on the given curve.
        """
        curve = curve if curve is not None else default_curve()
        curve_nid = backend._elliptic_curve_to_nid(curve)

        group = openssl._get_ec_group_by_curve_nid(curve_nid)
        order = openssl._get_ec_order_by_curve_nid(curve_nid)

        new_rand_bn = openssl._get_new_BN()
        rand_res = backend._lib.BN_rand_range(new_rand_bn, order)
        backend.openssl_assert(rand_res == 1)

        if not openssl._bn_is_on_curve(new_rand_bn, curve_nid):
            new_rand_bn = cls.gen_rand(curve=curve)
            return new_rand_bn

        return cls(new_rand_bn, curve_nid, group, order)

    @classmethod
    def from_int(cls, num, curve: ec.EllipticCurve=None):
        """
        Returns a CurveBN object from a given integer on a curve.
        """
        curve = curve if curve is not None else default_curve()
        try:
            curve_nid = backend._elliptic_curve_to_nid(curve)
        except AttributeError:
            # Presume that the user passed in the curve_nid
            curve_nid = curve

        group = openssl._get_ec_group_by_curve_nid(curve_nid)
        order = openssl._get_ec_order_by_curve_nid(curve_nid)

        conv_bn = openssl._int_to_bn(num, curve_nid)

        return cls(conv_bn, curve_nid, group, order)

    @classmethod
    def hash_to_bn(cls, *crypto_items, params=None, _iteration=0):
        params = params if params is not None else default_params()

        # TODO: Clean this in an upcoming cleanup of pyUmbral
        blake2b = hashes.Hash(hashes.BLAKE2b(64), backend=backend)
        for item in crypto_items:
            try:
                item_bytes = item.to_bytes()
            except AttributeError:
                if not isinstance(item, bytes):
                    raise TypeError("{} is not acceptable type, received {}".format(item, type(item)))
                item_bytes = item
            blake2b.update(item_bytes)
        
        blake2b.update(_iteration.to_bytes(4, byteorder='big'))

        hash_digest = blake2b.finalize()
        hash_digest = int.from_bytes(hash_digest, byteorder='big', signed=False)

        curve_nid = backend._elliptic_curve_to_nid(params.curve)
        order = openssl._get_ec_order_by_curve_nid(curve_nid)

        try:
            hash_digest = openssl._int_to_bn(hash_digest)
            bn_from_hash = openssl._get_new_BN()
            with backend._tmp_bn_ctx() as bn_ctx:
                res = backend._lib.BN_mod(
                    bn_from_hash, hash_digest, order, bn_ctx
                )
                backend.openssl_assert(res == 1)

            group = openssl._get_ec_group_by_curve_nid(curve_nid)
            bn_from_hash = cls(bn_from_hash, curve_nid, group, order)

        except ValueError:
            # This case is only reached when the result is 0, 
            # which happens with prob. 1/order of the curve
            _iteration += 1
            bn_from_hash = cls.hash_to_bn(*crypto_items, params, _iteration=_iteration)

        return bn_from_hash

    @classmethod
    def from_bytes(cls, data, curve: ec.EllipticCurve=None):
        """
        Returns a CurveBN object from the given byte data that's within the size
        of the provided curve's order.
        """
        curve = curve if curve is not None else default_curve()
        num = int.from_bytes(data, 'big')

        return cls.from_int(num, curve)

    def to_bytes(self):
        """
        Returns the CurveBN as bytes.
        """
        size = backend._lib.BN_num_bytes(self.order)

        return int.to_bytes(int(self), size, 'big')

    def __int__(self):
        """
        Converts the CurveBN to a Python int.
        """
        return backend._bn_to_int(self.bignum)

    def __eq__(self, other):
        """
        Compares the two BIGNUMS or int.
        """
        if type(other) == int:
            other = openssl._int_to_bn(other)
            other = CurveBN(other, None, None, None)

        # -1 less than, 0 is equal to, 1 is greater than
        return not bool(backend._lib.BN_cmp(self.bignum, other.bignum))

    def __pow__(self, other):
        """
        Performs a BN_mod_exp on two BIGNUMS.

        WARNING: Not in constant time yet.
        """
        if type(other) == int:
            other = openssl._int_to_bn(other)
            other = CurveBN(other, None, None, None)

        power = openssl._get_new_BN()
        with backend._tmp_bn_ctx() as bn_ctx:
            res = backend._lib.BN_mod_exp(
                power, self.bignum, other.bignum, self.order, bn_ctx
            )
            backend.openssl_assert(res == 1)

        return CurveBN(power, self.curve_nid, self.group, self.order)

    def __mul__(self, other):
        """
        Performs a BN_mod_mul between two BIGNUMS.
        """
        if type(other) != CurveBN:
            return NotImplemented

        product = openssl._get_new_BN()
        with backend._tmp_bn_ctx() as bn_ctx:
            res = backend._lib.BN_mod_mul(
                product, self.bignum, other.bignum, self.order, bn_ctx
            )
            backend.openssl_assert(res == 1)

        return CurveBN(product, self.curve_nid, self.group, self.order)

    def __truediv__(self, other):
        """
        Performs a BN_div on two BIGNUMs (modulo the order of the curve).

        WARNING: Not in constant time yet.
        """
        product = openssl._get_new_BN()
        with backend._tmp_bn_ctx() as bn_ctx:
            inv_other = backend._lib.BN_mod_inverse(
                backend._ffi.NULL, other.bignum, self.order, bn_ctx
            )
            backend.openssl_assert(inv_other != backend._ffi.NULL)

            res = backend._lib.BN_mod_mul(
                product, self.bignum, inv_other, self.order, bn_ctx
            )
            backend.openssl_assert(res == 1)

        return CurveBN(product, self.curve_nid, self.group, self.order)

    def __add__(self, other):
        """
        Performs a BN_mod_add on two BIGNUMs.
        """
        op_sum = openssl._get_new_BN()
        with backend._tmp_bn_ctx() as bn_ctx:
            res = backend._lib.BN_mod_add(
                op_sum, self.bignum, other.bignum, self.order, bn_ctx
            )
            backend.openssl_assert(res == 1)

        return CurveBN(op_sum, self.curve_nid, self.group, self.order)

    def __sub__(self, other):
        """
        Performs a BN_mod_sub on two BIGNUMS.
        """
        diff = openssl._get_new_BN()
        with backend._tmp_bn_ctx() as bn_ctx:
            res = backend._lib.BN_mod_sub(
                diff, self.bignum, other.bignum, self.order, bn_ctx
            )
            backend.openssl_assert(res == 1)

        return CurveBN(diff, self.curve_nid, self.group, self.order)

    def __invert__(self):
        """
        Performs a BN_mod_inverse.

        WARNING: Not in constant time yet.
        """
        with backend._tmp_bn_ctx() as bn_ctx:
            inv = backend._lib.BN_mod_inverse(
                backend._ffi.NULL, self.bignum, self.order, bn_ctx
            )
            backend.openssl_assert(inv != backend._ffi.NULL)
            inv = backend._ffi.gc(inv, backend._lib.BN_clear_free)

        return CurveBN(inv, self.curve_nid, self.group, self.order)

    def __mod__(self, other):
        """
        Performs a BN_nnmod on two BIGNUMS.
        """
        if type(other) == int:
            other = openssl._int_to_bn(other)
            other = CurveBN(other, None, None, None)

        rem = openssl._get_new_BN()
        with backend._tmp_bn_ctx() as bn_ctx:
            res = backend._lib.BN_nnmod(
                rem, self.bignum, other.bignum, bn_ctx
            )
            backend.openssl_assert(res == 1)

        return CurveBN(rem, self.curve_nid, self.group, self.order)
