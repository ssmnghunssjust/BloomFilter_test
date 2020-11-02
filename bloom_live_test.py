# -*- encoding: utf-8 -*-
from __future__ import absolute_import
import math
import hashlib
from pybloom_live.utils import range_fn, running_python_3
from struct import unpack, pack, calcsize

__version__ = '2.0'
__author__ = "Jay Baird <jay.baird@me.com>, Bob Ippolito <bob@redivi.com>,\
               Marius Eriksen <marius@monkey.org>,\
               Alex Brasetvik <alex@brasetvik.com>,\
               Matt Bachmann <bachmann.matt@gmail.com>,\
              "


def make_hashfuncs(num_slices, num_bits):
    if num_bits >= (1 << 31):
        fmt_code, chunk_size = 'Q', 8
    elif num_bits >= (1 << 15):
        fmt_code, chunk_size = 'I', 4
    else:
        fmt_code, chunk_size = 'H', 2
    total_hash_bits = 8 * num_slices * chunk_size
    if total_hash_bits > 384:
        hashfn = hashlib.sha512
    elif total_hash_bits > 256:
        hashfn = hashlib.sha384
    elif total_hash_bits > 160:
        hashfn = hashlib.sha256
    elif total_hash_bits > 128:
        hashfn = hashlib.sha1
    else:
        hashfn = hashlib.md5
    fmt = fmt_code * (hashfn().digest_size // chunk_size)
    num_salts, extra = divmod(num_slices, len(fmt))
    if extra:
        num_salts += 1
    salts = tuple(hashfn(hashfn(pack('I', i)).digest()) for i in range_fn(stop=num_salts))

    def _make_hashfuncs(key):
        if running_python_3:
            if isinstance(key, str):
                key = key.encode('utf-8')
            else:
                key = str(key).encode('utf-8')
        else:
            if isinstance(key, str):
                key = key.encode('utf-8')
            else:
                key = str(key)
        i = 0
        for salt in salts:
            h = salt.copy()
            h.update(key)
            for uint in unpack(fmt, h.digest()):
                yield uint % num_bits
                i += 1
                if i >= num_slices:
                    return

    return _make_hashfuncs


class BloomFilter(object):
    FILE_FMT = b'<dQQQQ'

    def __init__(self, capacity, error_rate=0.001, conn=None, redis_key='BloomFilter'):
        if not (0 < error_rate < 1):
            raise ValueError("Error_Rate must be between 0 and 1.")
        if not capacity > 0:
            raise ValueError("Capacity must be > 0")
        # given M = num_bits, k = num_slices, P = error_rate, n = capacity
        #       k = log2(1/P)
        # solving for m = bits_per_slice
        # n ~= M * ((ln(2) ** 2) / abs(ln(P)))
        # n ~= (k * m) * ((ln(2) ** 2) / abs(ln(P)))
        # m ~= n * abs(ln(P)) / (k * (ln(2) ** 2))
        num_slices = int(math.ceil(math.log(1.0 / error_rate, 2)))
        bits_per_slice = int(math.ceil(
            (capacity * abs(math.log(error_rate))) /
            (num_slices * (math.log(2) ** 2))))
        self._setup(error_rate, num_slices, bits_per_slice, capacity, 0)
        self.redis = conn
        self.redis_key = redis_key

    def _setup(self, error_rate, num_slices, bits_per_slice, capacity, count):
        self.error_rate = error_rate
        self.num_slices = num_slices
        print("切片个数=%s" % self.num_slices)
        self.bits_per_slice = bits_per_slice
        self.capacity = capacity
        self.num_bits = num_slices * bits_per_slice
        print("总字节数=%s" % self.num_bits)
        # 需要的多少M内存
        self.mem = math.ceil(self.num_bits / 8 / 1024 / 1024)
        self.blocknum = math.ceil(self.mem / 512)
        self.count = count
        self.make_hashes = make_hashfuncs(self.num_slices, self.bits_per_slice)

    def __contains__(self, key):
        """Tests a key's membership in this bloom filter.
        >>> b = BloomFilter(capacity=100)
        >>> b.add("hello")
        False
        >>> "hello" in b
        True
        """
        bits_per_slice = self.bits_per_slice
        name = self.redis_key
        hashes = self.make_hashes(key)
        offset = 0
        for k in hashes:
            if not self.redis.getbit(name, offset + k):
                return False
            offset += bits_per_slice
        return True

    def __len__(self):
        """Return the number of keys stored by this bloom filter."""
        return self.count

    def add(self, key, skip_check=False):
        """ Adds a key to this bloom filter. If the key already exists in this
        filter it will return True. Otherwise False.
        >>> b = BloomFilter(capacity=100)
        >>> b.add("hello")
        False
        >>> b.add("hello")
        True
        >>> b.count
        1
        """
        name = self.redis_key
        bits_per_slice = self.bits_per_slice
        hashes = self.make_hashes(key)
        found_all_bits = True
        if self.count > self.capacity:
            raise IndexError("BloomFilter is at capacity")
        offset = 0
        for k in hashes:
            if not skip_check and found_all_bits and not self.redis.getbit(name, offset + k):
                found_all_bits = False
            print("add name={} hash={}".format(name, offset + k))
            self.redis.setbit(name, offset + k, 1)
            offset += bits_per_slice

        if skip_check:
            self.count += 1
            return False
        elif not found_all_bits:
            self.count += 1
            return False
        else:
            return True

    def __getstate__(self):
        d = self.__dict__.copy()
        del d['make_hashes']
        return d

    def __setstate__(self, d):
        self.__dict__.update(d)
        self.make_hashes = make_hashfuncs(self.num_slices, self.bits_per_slice)


if __name__ == '__main__':
    import redis

    pool = redis.ConnectionPool(host='127.0.0.1', port=6379, password='', db=0)
    conn = redis.StrictRedis(connection_pool=pool)
    bf = BloomFilter(capacity=150, error_rate=0.001, conn=conn)
    for i in range(0, bf.capacity):
        _ = bf.add(str(i))
    print('inserted len: %s'%bf.__len__())
    print('%s is in the bloomfilter?    %s'%(149,149 in bf))