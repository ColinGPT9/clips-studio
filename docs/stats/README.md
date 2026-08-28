# Audience numbers

Written daily by [`.github/workflows/stats.yml`](../../.github/workflows/stats.yml).
Nothing here comes from the app: no telemetry ships to users, and these are
only figures GitHub already computed about this repository.

It exists because **GitHub keeps 14 days of traffic and then deletes it.**
Download totals are cumulative and survive; views and clones fall off the end of
a rolling window. The launch week is already gone. A sponsor asks for growth
over time, and that can only exist if something writes it down every day.

## `traffic.csv`

One row per day.

| Column | Meaning |
|---|---|
| `views` | Page views of the repository |
| `views_unique` | Distinct visitors, de-duplicated **by GitHub**, not by us |
| `clones` | `git clone` operations |
| `clones_unique` | Distinct cloners |

## `releases.csv`

One row per release asset per day, holding a **cumulative** total — GitHub only
ever reports a running count. Storing it daily is what makes the curve
recoverable: the difference between two dates is that period's downloads.

## What these numbers are not

Being wrong about this in front of a sponsor is worse than having smaller
numbers, so:

- **A download is not an install.** The file counted is the ~800 KB web setup.
  Completing an install means downloading a 5.8 GB payload from Hugging Face
  afterwards, and many downloads will not get that far.
- **Hugging Face cannot tell us how many did.** Its download counter only tracks
  library-recognised files and reports 0 for raw URL fetches, so the payload —
  the closest thing to a true install count — is invisible.
- **Clones are not people.** CI, mirrors and bots clone. `clones_unique` is
  closer, still not a headcount.
- **Unique views are GitHub's definition of unique**, over a day, and not
  comparable to a web analytics "unique visitor".
- **None of this measures active users.** Nothing here says whether anyone
  opened the app twice. Every running copy does poll `latest.yml` for updates,
  which would be a heartbeat needing no new telemetry, but Hugging Face exposes
  no request logs — and counting it would mean putting a service in the desktop
  update path, which this project deliberately does not do.
- **winget publishes no per-package install counts.** Being in winget is not
  measurable from outside.

## The number that will be better than all of these

Once the Microsoft Store listing is live, **Partner Center reports acquisitions
and daily/monthly active users**: a real install count and a real retention
figure, produced by Microsoft, needing no telemetry of ours. When quoting
figures to a sponsor, that is the stronger source and this directory is the
supporting trend.
