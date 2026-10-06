"""Plugins: pipelines from outside Clips Kitty, run as separate processes.

A plugin is a folder with a manifest (`clipskitty.yaml`) and a command. When
a job names one (`pipeline: {id, version, settings}`), process_video asks it
for the video's moments instead of scoring them itself, and makes the clips
as usual. The plugin never runs inside this process: plugins/runner.py starts
it with a job folder and reads its answer back (the contract is in
sdk/python/clipskitty_sdk/contract.py, the same files a plugin developer uses).

    store.py     what is installed, as the plugin manager leaves it on disk
    runner.py    one plugin run for one video
    _sdk.py      makes the SDK importable here, from the checkout or the bundle
"""
