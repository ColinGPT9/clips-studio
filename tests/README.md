# Tests

```
pip install pytest
pytest
```

## What is tested, and what deliberately isn't

These cover the **deterministic** parts of the engine — the code where a
given input must always produce the same output, and where a regression is
silent rather than loud.

That is a real limit, so it's worth stating plainly: **nothing here can tell
you a clip came out well.** Clip quality depends on a language model, real
footage and a GPU. It is judged by watching videos, and it always will be.

What these tests *do* protect is the layer underneath, where past bugs
actually lived:

| Area | The bug it would have caught |
|---|---|
| `test_creator_knowledge.py` | A line said once becoming a permanent "catchphrase" |
| `test_transcript_repair.py` | A Whisper repetition loop scored as if it were speech |
| `test_paths.py` | An installed app writing videos into Program Files |
| `test_binaries.py` | FFmpeg resolved from PATH instead of the bundled copy |
| `test_preflight.py` | A missing dependency surfacing as a stack trace mid-render |

Every one of those shipped at some point. They are cheap to test and
expensive to notice.

## Fixtures and assets

`conftest.py` provides a real (empty) `db`, a `creator` row, a short
`segments` transcript, plus two things backed by
[`assets/`](assets/README.md):

| Fixture | What it gives you |
|---|---|
| `sample_transcript` | Two minutes of written-out stream, parsed into `Segment`s — including a genuinely repeated phrase *and* a vivid one-off that must not be mistaken for one |
| `sample_video` | The generated test video, or a **skip** if it has not been built |

`sample_video` skips rather than fails on purpose: the file is gitignored and
built on demand, so a contributor who has not run the generator has not
broken anything, and CI has no FFmpeg to run it with.

```
python tests/assets/make_sample_video.py
```

## Writing more

Keep them **fast and offline**. No model calls, no network, no real video —
CI has no GPU and no Ollama. Need a model? Use `FakeBackend` from
[`../examples/fake_backend.py`](../examples/fake_backend.py), which returns a
fixed reply and records the prompts it was handed. If a test genuinely needs
media, mark it:

```python
@pytest.mark.slow
def test_something_with_real_footage():
    ...
```

and it stays out of the default run.
