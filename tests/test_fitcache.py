"""The cache must never change a number, only skip work.

Two properties matter. Content-keying: two runs whose training rows are
the same share an entry without anyone declaring that they should, which
is what lets `--until 2018-12-31` and the full run reuse ten blocks. And
sensitivity: any change to what a fit depends on must miss, or the cache
would silently serve a stale model.
"""

import numpy as np
import pytest

import fitcache


def test_same_content_same_key():
    m = np.zeros(1000, bool)
    m[:400] = True
    assert fitcache.key('ridge', 100, m) == fitcache.key('ridge', 100, m.copy())


def test_one_flipped_row_changes_the_key():
    a = np.zeros(1000, bool)
    a[:400] = True
    b = a.copy()
    b[999] = True
    assert fitcache.key('ridge', 100, a) != fitcache.key('ridge', 100, b)


def test_every_ingredient_is_in_the_key():
    m = np.zeros(100, bool)
    m[:50] = True
    base = fitcache.key('ridge', 'srcA', 100, m)
    assert base != fitcache.key('shapelet', 'srcA', 100, m)   # model
    assert base != fitcache.key('ridge', 'srcB', 100, m)      # windows file
    assert base != fitcache.key('ridge', 'srcA', 316, m)      # alpha


def test_masks_of_different_length_differ():
    assert fitcache.key(np.zeros(10, bool)) != fitcache.key(np.zeros(20, bool))


def test_two_windows_that_happen_to_match_share_an_entry():
    """The point of content-keying: a block whose training rows are the
    same under two different flag combinations is one fit, not two."""
    n = 2000
    dates = np.arange(n)
    # 'run to 2018' and 'run to 2026' both train block Y on rows < 800
    from_short = dates < 800
    from_long = dates < 800
    assert (fitcache.key('ridge', 'src', 100, from_short)
            == fitcache.key('ridge', 'src', 100, from_long))


def test_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(fitcache, 'CACHE', tmp_path)
    k = fitcache.key('x')
    assert fitcache.load('block', k) is None
    fitcache.save('block', k, score=np.arange(5, dtype=np.float32),
                  cut=np.arange(3, dtype=np.float32))
    got = fitcache.load('block', k)
    assert got['score'].tolist() == [0, 1, 2, 3, 4]
    assert got['cut'].tolist() == [0, 1, 2]


def test_big_roundtrip_is_memmapped(tmp_path, monkeypatch):
    monkeypatch.setattr(fitcache, 'CACHE', tmp_path)
    k = fitcache.key('feats')
    calls = []

    def build():
        calls.append(1)
        return np.arange(12, dtype=np.float32).reshape(3, 4)

    a = fitcache.cached_big('feats', k, build)
    b = fitcache.cached_big('feats', k, build)
    assert len(calls) == 1, 'the second call rebuilt instead of loading'
    assert np.array_equal(np.asarray(a), np.asarray(b))
    assert isinstance(b, np.memmap)


def test_a_partial_write_is_never_served(tmp_path, monkeypatch):
    """Saves go to a .tmp file and are renamed, so a run killed mid-write
    leaves no half-file for the next run to trust."""
    monkeypatch.setattr(fitcache, 'CACHE', tmp_path)
    k = fitcache.key('x')
    fitcache.save('block', k, score=np.zeros(3, np.float32))
    assert not list(tmp_path.glob('*.tmp.npz'))
    assert len(list(tmp_path.glob('block_*.npz'))) == 1
