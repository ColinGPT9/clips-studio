"""Which provider and which model a backend spec names.

A spec is "<provider>/<model>". Local models are "ollama/gemma:7b". Cloud
models keep their own slashes, "openrouter/meta/muse-spark-1.3", so the
provider is everything before the FIRST slash and the model everything after
it; `spec.split("/")[-1]` would turn that into "muse-spark-1.3" and lose the
vendor. A bare "gemma:7b" is a local model, as load_config has always read it.
"""

LOCAL = "ollama"


def parse_spec(spec: str) -> tuple[str, str]:
    spec = (spec or "").strip()
    provider, sep, model = spec.partition("/")
    if not sep:
        return LOCAL, spec
    return provider, model


def is_local(spec: str) -> bool:
    """True for Ollama, including an empty spec: the app's default is local."""
    return parse_spec(spec)[0] == LOCAL
