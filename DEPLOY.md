# Deploying the product to Cloud Run

The first deploy needs a human. Creating the service, enabling APIs and granting
IAM are login-gated console actions — the `LOGIN` class `CHARTER.md` §4 puts
behind the Operator — and Coral has no shell and no `gcloud`. After this, deploys
can be automated (see the last section).

Everything below runs in **Cloud Shell**, which is already authenticated and
needs nothing installed locally. Open it from the Cloud console, top right.

Roughly ten minutes.

---

## 0. Which project

**`undra-504613`**, not `undra-free`. The project *name* is `undra`; the **id** carries a numeric suffix because ids are globally unique, and it is the id every command below needs. The product handles user data and must use the paid
key — `invariants.toml` pins `user_data_key = "paid"` and `CHARTER.md` §3.4 is
why: free-tier prompts may be used for training, and users photograph letters
carrying their name, address and personnummer.

```bash
gcloud config set project undra-504613
```

---

## 1. Turn on what the build needs

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com
```

Covered by the trial credit on `My Billing Account`. The Gemini API is not — that
bills against the prepaid balance separately, as `HANDOFF.md` §4 records.

---

## 2. Somewhere to put the image

```bash
gcloud artifacts repositories create undra \
  --repository-format=docker \
  --location=europe-north1 \
  --description="undra product images"
```

`europe-north1` is Finland, the closest region to Lund. If you change it, change
`_REGION` in `cloudbuild.yaml` to match.

---

## 3. The Gemini key, in Secret Manager rather than an env var

A value passed with `--set-env-vars` is readable by anyone who can run
`gcloud run services describe`. Secret Manager keeps it out of the service
description and out of the build logs.

```bash
read -rs GEMINI_KEY          # paste the PAID key, press Enter — nothing is echoed
printf %s "$GEMINI_KEY" | gcloud secrets create undra-gemini-key \
  --replication-policy=automatic --data-file=-
unset GEMINI_KEY
```

`printf %s` rather than `echo`, and `--data-file=-` fed from a variable rather
than from a paste, because both alternatives capture a trailing newline. A Gemini
key with `\n` on the end fails authentication with errors that say nothing about
whitespace. Check the length before going further — it should be exactly 53:

```bash
gcloud secrets versions access latest --secret=undra-gemini-key | wc -c
```

The key is the **paid** one, from `undra-504613` — the same value as
`GOOGLE_API_KEY` in `env/app.env`. Copy it from AI Studio rather than off the lab
box. Never the free key: `invariants.toml` pins `user_data_key = "paid"`.

Then let the runtime service account read it:

```bash
PROJECT_NUMBER=$(gcloud projects describe undra-504613 --format='value(projectNumber)')
gcloud secrets add-iam-policy-binding undra-gemini-key \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role=roles/secretmanager.secretAccessor
```

And give the build account what it needs. **This is the step that fails on a
new project.** Google stopped provisioning the legacy
`PROJECT_NUMBER@cloudbuild.gserviceaccount.com` with broad permissions for
projects created after roughly mid-2024, so builds run as the Compute Engine
default account with almost nothing granted. The first symptom is a complaint
about `storage.objects.get` — the build cannot read the source tarball it just
uploaded, which reads like a bug in the build rather than a missing role.

```bash
SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

for ROLE in \
  roles/cloudbuild.builds.builder \
  roles/storage.objectViewer \
  roles/artifactregistry.writer \
  roles/logging.logWriter \
  roles/run.admin \
  roles/iam.serviceAccountUser
do
  gcloud projects add-iam-policy-binding undra-504613 \
    --member="serviceAccount:${SA}" --role="$ROLE" --condition=None --quiet
done
```

If an error names a different service account than `$SA`, grant the roles to
whichever one it names — some projects route builds elsewhere. IAM changes take
up to a minute, so an immediate identical failure is worth one retry before
assuming the grants did not apply.

---

## 4. Build and deploy

```bash
git clone https://github.com/ElizaZadura/undra.git && cd undra
gcloud builds submit --config cloudbuild.yaml
```

`cloudbuild.yaml` exists because this repository has **two** Dockerfiles. The one
at the root builds the operator container that runs on the lab box;
`app/Dockerfile` builds the product. A bare `gcloud run deploy --source .` picks
the root one and deploys the agent runner as a web service, which is not what
anybody wants.

---

## 5. Tell the watchdog where it lives

The deploy prints a URL like `https://undra-xxxxxxxx-lz.a.run.app`. Put the host
— no scheme, no trailing slash — into `invariants.toml`:

```toml
allowed_hosts    = ["undra-xxxxxxxx-lz.a.run.app"]
health_path      = "/api/health"
```

Until this is set, `situation_report.py` reports `deploy_health: UNKNOWN` with
"no host configured yet — nothing is deployed", which is honest but means the
loop cannot tell whether the product is up.

Check it yourself first:

```bash
curl -s https://<host>/api/health
```

`health_path` is `/api/health` because that is what the app serves. It was
`/healthz` until 2026-08-07, which would have reported a perfectly healthy
deployment as down and eventually halted the loop over nothing.

---

## 6. Then commit and let a cycle pick it up

```bash
git add invariants.toml && git commit -m "Point the watchdog at the deployed service"
git push
```

The next cycle reads `allowed_hosts`, polls the health endpoint, and starts
recording `deploy_health` in every situation report.

---

## Afterwards: automatic deploys

Once the above works by hand once, a GitHub Actions workflow can run the same
`gcloud builds submit` on every push to `main`, so Coral's merges deploy
themselves. That needs a way for Actions to authenticate to GCP — Workload
Identity Federation is the version that involves no key file. Worth doing after
the first manual deploy proves the pipeline, not before: debugging a build and
debugging federated auth at the same time is twice the work and half the
information.
