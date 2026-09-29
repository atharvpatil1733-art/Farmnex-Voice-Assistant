# FarmNex as the host app

Checked against `atharvpatil1733-art/farmnex_main` on 2026-09-29. This file says how this kit is
ported into FarmNex (PORTING.md sections A and C). The golden rules in `CLAUDE.md` still apply —
especially rule 1: nothing FarmNex-specific goes into `backend/voice_core/` or the Flutter package's
`lib/`; it goes into `domain_packs/` or into the FarmNex repo.

The host-side guide is `docs/integration/voice-assistant.md` in farmnex_main.

## Facts about the FarmNex backend

| Topic | FarmNex |
|---|---|
| Login | FarmNex's **own** phone-OTP login, **not Supabase Auth**. Access tokens are RS256 JWTs: `iss=farmnex-api`, `aud=farmnex-mobile`, `sub` = the user's `public_id` (UUID), `type=access`, `role`, 15-minute expiry. The same key also signs `type=refresh` (30 days) and `type=registration` tokens. |
| API | `https://farmnex.fastapicloud.dev`, everything under `/api/v2`. The voice tools will call `/api/v2/voice-tools/...` (built in farmnex_main to return exactly this pack's tool shapes). |
| Ownership | The FarmNex API decides whose data is returned from the forwarded token. Use `HOST_API_AUTH_MODE=forward_user_jwt`. |
| Database | FarmNex's Supabase project allows only added prefixed tables — no new schemas or extensions. So this kit's `0001_voice_core.sql` (schema `voice`, `vector` + `pgcrypto` extensions) runs in a **separate free Supabase project**. |
| Crops | Only **tomato** is supported by every FarmNex part. FarmNex main: rice, wheat, maize, tomato, potato, onion, cotton, sugarcane, groundnut, mango, banana, carrot (+ Crop Rescue crops being added). Crop Rescue: tomato, spinach, okra, brinjal, cauliflower, grapes, capsicum, cucumber. Forecaster: onion, tomato, potato. |
| Crop Rescue | Works on *lots* (crop, kg, harvest time, location, storage), checks spoilage every 12 h, raises alerts and suggests nearby buyers. There is no "put a listing into rescue with days_left" action. |
| Pre-bidding | 7-day bidding; the winning buyer pays a 20% advance into the wallet; the farmer is paid after the buyer receives the produce. (Who picks the winner — farmer accepts vs. auto at close — is an open decision in farmnex_main `docs/STATUS.md`.) |
| App name | FarmNex (not BhumiBij). |

## Changes to make in this repo (in order)

1. **RS256 JWT `AuthVerifier` adapter** in `backend/voice_core/adapters/jwt_rs256/` (generic — no
   FarmNex words). Config: `AUTH_PROVIDER=jwt_rs256`, `JWT_PUBLIC_KEY_PEM` or `JWT_PUBLIC_KEY_PATH`,
   `JWT_ISSUER`, `JWT_AUDIENCE`, `JWT_REQUIRED_TYPE=access`. Verify signature, `exp`, `iss`, `aud`,
   and **reject any token whose `type` isn't `access`**. `Principal(user_ref=sub)`. With this adapter
   configured, `APP_ENV=prod` must start. Tests with a locally generated key pair (valid, expired,
   wrong issuer, wrong audience, refresh-type token → rejected).
2. **Pack facts:** `app_name: "FarmNex"`; knowledge docs match the facts above; add Marathi
   `knowledge/mr/crop-rescue.md` and `knowledge/mr/pre-bidding.md`.
3. **Crop enum:** `tomato, onion, potato` + the Crop Rescue crops (spinach, okra, brinjal,
   cauliflower, grapes, capsicum, cucumber); labels for them in `answer_labels.crops` (hi/mr/en).
   Remove soybean and pomegranate. Update fixtures and evals that use removed crops.
4. **Crop Rescue tools:** replace `request_crop_rescue` with read tools `get_rescue_alerts` and
   `get_rescue_matches(lot_ref)` (optional write: `mark_rescue_lot_sold(lot_ref)`); new fixtures;
   update the eval cases that used the old tool.
5. **Switch tools to `http`** only when farmnex_main has the matching `/api/v2/voice-tools/...`
   endpoint (its guide lists which tool depends on what). Read tools first:
   `get_demand_forecast` (`timeout_s: 12` — the forecaster can be slow to wake),
   `get_rescue_alerts`, `get_rescue_matches`, `get_pickup_status`. Write tools only after
   farmnex_main's F12. Run the evals in live mode against a test farmer account.
6. **Deploy:** separate Supabase project for `DATABASE_URL`; `uvicorn app.main:app
   --ws-max-size 65536`; `HOST_API_BASE_URL=https://farmnex.fastapicloud.dev`. Free hosts sleep —
   warm up before judging; test a laptop + tunnel fallback once.
7. **LLM capacity for judging:** the free chain allows ~1 turn/min per Groq model — use a paid key or
   a longer `LLM_FALLBACK_CHAIN`, and re-run `voice_core.evals.latency` on the demo network.
8. **M5 without the Supabase-login demo app:** build the `voice_assistant` Flutter package with a
   `tokenProvider` callback (FarmNex passes its access token), `auth.refresh` on token refresh, and
   close codes 4400/4401/4403 handled. FarmNex adds the package as a git dependency and puts the mic
   button in its assistant dialog.

## Time budget (FarmNex has a 40–50 h build window; voice is a stretch goal)

Read-only version ≈ 10–12 h in total across both repos (this repo: changes 1–6 ≈ 3–4 h, M5 package
≈ 3–4 h). Write tools add 3–4 h and wait for FarmNex F12. Smallest fallback (≈ 4 h): changes 1–6 and
demo from a laptop with the CLI client using a real FarmNex farmer token.
