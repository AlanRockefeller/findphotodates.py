import os
import tempfile
import findphotodates as fpd
from findphotodates import TSV_COLUMNS


def test_load_cache_quote():
    with tempfile.NamedTemporaryFile("w", delete=False) as f:
        f.write("\t".join(TSV_COLUMNS) + "\n")
        f.write('"My ""photo"".jpg"\t2023:01:01 12:00:00\t1024\t0\t\t\t\t\n')
        fname = f.name

    try:
        cache = fpd.load_cache(fname)
        assert 'My "photo".jpg' in cache
    finally:
        if os.path.exists(fname):
            os.unlink(fname)
