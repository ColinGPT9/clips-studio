# Built with Clips Kitty

Projects built with or for Clips Kitty are listed in
**[Awesome Clips Kitty](https://github.com/ColinGPT9/awesome-clips-kitty)**, a curated list of
pipelines, plugins, integrations, apps and tools, in its own repository. It includes
the official ones: the Clips Kitty OBS Plugin, the MCP server and agent skill,
and the Python SDK. The Clips Kitty Marketplace shows the same list.

Clips Kitty runs on your own PC and exposes the same API its app uses. Anything
that can make an HTTP request can queue a video, follow its progress and read
the clips it made. Start with [docs/API.md](docs/API.md).

## Add your project

Add one small YAML file with a pull request.
[CONTRIBUTING.md](https://github.com/ColinGPT9/awesome-clips-kitty/blob/main/CONTRIBUTING.md) has the format and the
inclusion criteria. A separate app or tool that uses the API or the SDK is
"built with Clips Kitty"; a pipeline that runs inside Clips Kitty is a listing,
installed from the Marketplace
([Marketplace publishing](docs/developers/marketplace-publishing.md)).

Two things matter most:

- **It uses the documented API.** Build on the endpoints in
  [docs/API.md](docs/API.md), not by reading the database or the data folder
  directly, which change without notice.
- **It is upfront about data.** If it sends anyone's videos, clips or accounts
  anywhere, it says so, and its entry says so too.

Add the `clips-kitty` topic to your repository too, so people can find it on
GitHub even before it is listed.
