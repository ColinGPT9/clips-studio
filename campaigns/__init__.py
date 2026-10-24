"""Clipping campaigns: submitting a published clip's public link to a campaign
that pays for views, in Discord or on the campaign's own website.

Clips Kitty never posts as the user. A campaign is submitted one of three ways:
by hand in Discord with the command and values prepared (discord_assisted, the
default), by a webhook a server admin authorised (discord_webhook), or by hand
on the campaign's page (website).

- model.py         statuses, their labels and groups, platforms, the campaign rules
- links.py         Discord channel, message and webhook links
- urls.py          a submission's public link: where it comes from, and checking one
- eligibility.py   what a campaign asks of a post, checked, and the verdict
- formatter.py     the message, the command and the webhook payload
- statemachine.py  which status changes are allowed, applied compare-and-set
- queue.py         evaluating a submission once its post's link is known

Standard library, `core.*`, `server.posts`, the publish and provider modules and
the plugin store's id pattern only, so CI's light job tests all of it. Nothing
here is ever passed to a plugin, and a pipeline can't import it
(_clipskitty_script_host.py ENGINE_PACKAGES).
"""
