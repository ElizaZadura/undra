# Undra — Lund Pre-Arrival Student Assistant MVP

Undra is a mobile-first web assistant and information portal designed for pre-arrival international students in Lund, Sweden. It provides clear, actionable instructions on navigating everyday Swedish systems (such as housing, Arrival Day travel, waste sorting, and laundry booking) while implementing rigid privacy controls and legal/safety guardrails.

---

## Features

1. **Lund Pre-Arrival Guide & FAQ**: Mobile-first visual guides on:
   - **Housing Tips**: Corridor living, AF Bostäder novisch lottery, and avoiding common rental scams.
   - **Arrival Day Logistics**: Traveling from Copenhagen Airport (CPH) to Lund C, ID/border checks, and key collection.
   - **Swedish Pant System**: Recycling aluminum cans and PET bottles at grocery stores for refunds.
   - **Waste Sorting**: Standard Swedish recycling rooms (Miljöhus) sorting guides.
   - **Public Transit**: Getting student discounts and boarding buses with the Skånetrafiken app.
   - **Laundry Room Booking**: Digital booking tags, punctuality, and the strict unwritten rules of "tvättstuga" cleanup.

2. **Interactive Q&A Assistant**:
   - Handles text queries and image/photo uploads (e.g., pictures of signs, notices, laundry machines).
   - Powered by Gemini API (`gemini-2.5-flash` or custom models).

3. **Deterministic Refusal & Route Guardrails (CHARTER §3.3)**:
   - Queries about **immigration/visas**, **Swedish taxes (folkbokföring/personnummer)**, **legal contract disputes**, and **medical/safety emergencies** are blocked locally and deterministically.
   - Users are explicitly refused advice on these sensitive topics and provided with direct link buttons to official authorities:
     - **Migrationsverket**
     - **Skatteverket**
     - **AF Bostäder & Lund University International Desk**
     - **1177 Vårdguiden**

4. **EU AI Act Transparency Disclosure (Article 50)**:
   - Highly visible AI-authorship badges across the interface.
   - Every AI-generated response starts with the explicit disclosure: `🤖 [AI-Generated Response by Undra Assistant]`.
   - Dedicated page metadata attributes specify AI authorship.

5. **GDPR-Compliant In-Memory Privacy**:
   - Automatically strips EXIF metadata from uploaded images on receipt.
   - Ephemeral in-memory image processing; uploaded files are **never written to disk or persistent storage**.

---

## Local Setup

### Prerequisites
- Python 3.11 or higher
- A Gemini API Key (from [Google AI Studio](https://aistudio.google.com/))

### Installation
1. Clone the repository and navigate to the project directory:
   ```bash
   cd undra
   ```
2. Install Python dependencies:
   ```bash
   pip install -r app/requirements.txt
   ```

### Running the Application Locally
1. Set your Gemini API Key in your terminal environment:
   ```bash
   export GEMINI_API_KEY="your-api-key-here"
   # or
   export GOOGLE_API_KEY="your-api-key-here"
   ```
2. Start the FastAPI uvicorn server:
   ```bash
   python -m uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
   ```
3. Open your browser and navigate to: [http://localhost:8080](http://localhost:8080)

---

## Running with Docker Compose

You can easily build and run Undra inside a local Docker container mimicking production:

1. Place your Gemini API Key in `env/app.env` as:
   ```env
   GOOGLE_API_KEY=your_actual_key
   ```
2. Launch the application:
   ```bash
   docker compose up --build app
   ```
3. Access the web interface at: [http://localhost:8080](http://localhost:8080)

---

## Running Tests

Undra features unit and integration tests covering the local guardrails, FastAPI routing, image metadata stripping, and mocked Gemini responses.

Run the test discovery suite cleanly with:
```bash
python3 -m unittest discover -s tests -v
```

---

## Deploying to Google Cloud Run

To deploy the application to Google Cloud Run, follow these steps:

1. Configure your GCP project and ensure billing is set up:
   ```bash
   gcloud config set project your-gcp-project-id
   ```
2. Enable necessary Google Cloud services (Artifact Registry, Cloud Build, Cloud Run):
   ```bash
   gcloud services enable run.googleapis.com build.googleapis.com artifactregistry.googleapis.com
   ```
3. Submit your build to Cloud Build:
   ```bash
   gcloud builds submit --tag gcr.io/your-gcp-project-id/undra-app:latest . --file app/Dockerfile
   ```
4. Deploy the container image to Cloud Run, injecting the Gemini API key securely:
   ```bash
   gcloud run deploy undra-app \
     --image gcr.io/your-gcp-project-id/undra-app:latest \
     --platform managed \
     --region europe-west1 \
     --set-env-vars="GOOGLE_API_KEY=your-api-key-here,UNDRA_APP_MODEL=gemini-2.5-flash" \
     --allow-unauthenticated \
     --port 8080
   ```
5. Note the generated Service URL from the command output. Configure your custom domain (`undra.nu`) in the Cloud Run panel if desired.
