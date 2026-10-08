"""A Clips Kitty pipeline that will find moments in a video. It finds none yet.

This is the blank template of the Clips Kitty SDK: add your own checks in
main(), in the marked block. Clips Kitty starts this file with a job folder;
to try it, run `python -m clipskitty_sdk run . --sample` in the plugin's
folder (`py -m clipskitty_sdk` in PowerShell).

Clips Kitty runs it on its own Python, which has the Python standard library
and clipskitty_sdk and nothing else, so import only those, at the top of the
file. Open text files with encoding="utf-8".
"""

from __future__ import annotations

from clipskitty_sdk import media, run


def main(job):
    info = media.probe(job)
    job.log(f"The video is {info.width}x{info.height} at {info.fps:g} frames a second")

    # ---- your checks: add a moment for each thing worth a clip ------------------------
    # For example, the stretches that are at least 6 dB louder than the half
    # minute around them (add signals to the import at the top):
    #
    #     loud = signals.spikes(media.loudness(job), louder_by=6.0)
    #     for start, end in signals.merge(signals.stretches(loud)):
    #         first, last = signals.around(job, start, end)
    #         job.add_range(first, last, label="loud", reason="the sound gets louder")
    #
    # media reads the video, signals turns numbers and frames into moments,
    # and text finds words in what is said (which needs transcript in inputs
    # and transcript.read in permissions).
    # ------------------------------------------------------------------------------------

    job.finish(notes="This is the blank template: add your own checks in src/main.py.")


if __name__ == "__main__":
    run(main)
