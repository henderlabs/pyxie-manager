# Feedback (Platform > Feedback)

Anyone signed in can report a bug, ask for a feature or send general feedback from **Platform > Feedback**, or the
**Send feedback** link at the bottom of the sidebar (which also records the page you were on).

Feedback goes to the public repository [henderlabs/pyxie-feedback](https://github.com/henderlabs/pyxie-feedback):

| Button | What happens |
|---|---|
| **Open on GitHub** | Opens a new issue with your title, description, PyXie version and diagnostics filled in. You review it on GitHub and submit it there. Needs a GitHub account. Nothing is sent by PyXie. |
| **Send by email** | Opens your own mail app with a message to the PyXie developers already filled in. You review it and send it from there. Nothing goes through this server's email settings. |
| **Copy as text** | For people without a GitHub account or a server with no internet access. |

**Diagnostics** (a checkbox, on by default) is a fixed list shown on the page before anything leaves: PyXie version, the
page, your browser, Proxmox version, node and guest counts, how many setup steps are done, and open finding counts. It
never includes hostnames, IP addresses, VM names, credentials or logs. Issues in the feedback repository are public.

This server keeps a local list of what was opened, emailed or copied (kind, title, who, when, channel). It does not keep
descriptions or diagnostics.

Known issues and the roadmap: [KNOWN_ISSUES.md](https://github.com/henderlabs/pyxie-feedback/blob/main/KNOWN_ISSUES.md).
