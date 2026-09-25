"""Cloud AI on the user's own API key ("bring your own key").

Clips Kitty is local first: Ollama and Whisper on this PC are the default and
nothing here changes them. This package is the opt-in for PCs that cannot run
the models, and it holds three rules:

- The key is the user's. There is no Clips Kitty key, account or proxy; a
  request goes from this PC straight to the provider, billed to the user.
- Nothing falls back. A failing provider is reported, never swapped for
  another provider or for a local model.
- A provider is data. catalog.PROVIDERS lists them; an OpenAI-compatible one
  is a single entry, and a new wire format is one file in adapters/.
"""
