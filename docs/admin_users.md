Admin — User Management

Overview
--------
This page documents the admin user management interface and the improvements made to match the rest of the admin UIs.

Key changes
-----------
- Consolidated per-user actions into a single dropdown to reduce visual clutter.
- Added links inside each user action dropdown to:
  - Edit the user
  - View the user
  - View the user's edit history
  - Delete the user (if you have the permission)
- Delete confirmation uses a shared `deleteModal.js` behavior with fast-delete (hold Shift to delete instantly).
- The edit page contains improved help text, clearer confirmed checkbox, and Cancel button.

Keyboard and shortcuts
----------------------
- Shift: toggle fast-delete mode (hold Shift then click Delete to bypass the confirmation modal).
- Escape: close an open modal.

Developer notes
---------------
- The user list template is `templates/admin_V2/user/list.html`.
- The edit page is `templates/admin_V2/user/edit.html`.
- Deletion uses the shared `static/js/deleteModal.js` which expects the confirm button to have id `confirmDelete` and sets `data-type` to `user`.


## What deleting a user removes

After the fresh step-up and the `users` delete succeed, `admin/user_erasure.py`
removes the account's direct personal-data records (settings, login and password
logs, reminders, attendance, own reports, notifications, messages, memberships,
MFA, magic links, API and developer records, scope grants, board clearances,
submitter rows, suggestions) and its links in other users' friend/follower lists
and demonstration editor lists. Kept on purpose: demonstrations the user
submitted, moderation `cases` and `city_admin_applications`, `malicious_reports`,
`security_cases` and the audit logs; their user id no longer resolves. The removed
counts are written to the application log, never the records themselves.
