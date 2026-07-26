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

## Writing more

Keep them **fast and offline**. No model calls, no network, no real video —
CI has no GPU and no Ollama. If a test genuinely needs media, mark it:

```python
@pytest.mark.slow
def test_something_with_real_footage():
    ...
```

and it stays out of the default run.
