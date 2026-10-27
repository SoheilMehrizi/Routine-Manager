# Cross-user isolation checks (run once the stack is up)
Get two session tokens (sign in as user A and user B in the browser; copy `mel_token` from localStorage).

1. As A: `PUT /api/tasks/abcd1234` with a JSON body -> 200.
2. As B: `GET /api/tasks` must not list it; `PUT /api/tasks/abcd1234` creates B's own separate record
   (ids are per user); `DELETE /api/tasks/abcd1234` as B must not remove A's.
3. As A again: the record is unchanged.
4. Body `{"user_id": 1}` or `{"id": "..."}` is ignored; the owner is always the token's user.
5. `GET /api/integrations` never contains a secret field, only `has_secret`.
6. `DELETE /api/me` as A removes A's records and integrations; B is untouched.
7. No token or a bad token -> 401 on every `/api/*` route.
