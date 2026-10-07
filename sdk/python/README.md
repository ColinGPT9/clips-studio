# Clips Kitty plugin SDK (`clipskitty_sdk`)

A small Python package for writing [Clips Kitty](https://github.com/ColinGPT9/clips-studio) pipelines: read the job, report progress, return moments, and check a plugin's manifest. It uses the Python standard library only (reading a YAML manifest also needs PyYAML).

**Licence: MIT** ([LICENSE](LICENSE)). Clips Kitty itself is AGPL-3.0-or-later; the SDK is MIT so that a plugin, app or tool built on it can use any licence its author chooses, open or closed. Using the SDK does not put your code under the AGPL.

```text
pip install "clipskitty-sdk[yaml] @ git+https://github.com/ColinGPT9/clips-studio#subdirectory=sdk/python"
python -m clipskitty_sdk validate path/to/your-plugin
python -m clipskitty_sdk run path/to/your-plugin --video video.mp4
```

Inside the app you never install it: Clips Kitty puts its own copy on your plugin's `PYTHONPATH`.

Documentation: [docs/developers/sdk.md](../../docs/developers/sdk.md), and the rest of [docs/developers/](../../docs/developers/README.md).
