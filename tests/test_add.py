import os
import tempfile
import findphotodates as fpd
from findphotodates import TSV_COLUMNS


class DummyArgs:
    hash_mode = "off"
    hash_exts = None
    use_hash_cache = False
    hash_cache_path = None
    sample_chunk_mib = 0
    sample_chunks = 0
    sample_algo = "blake2b"
    full_algo = "sha256"


def test_add_hashes():
    with tempfile.NamedTemporaryFile("w", delete=False) as f:
        f.write("\t".join(TSV_COLUMNS) + "\n")
        f.write('"My ""photo"".jpg"\t2023:01:01 12:00:00\t1024\t0\t\t\t\t\n')
        fname = f.name

    try:
        result = fpd.add_hashes_to_inventory(fname, DummyArgs())
        assert result is False, "hash_mode='off' should return False (early exit)"
    finally:
        if os.path.exists(fname):
            os.unlink(fname)
