import io
import os
import logging
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Form, File, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image

from app.guardrails import check_query_guardrails

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("undra-app")

app = FastAPI(
    title="undra - Lund Pre-Arrival Assistant",
    description="A mobile-first web assistant for pre-arrival international students in Lund.",
    version="1.0.0"
)

# CORS middleware for local testing/development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Locate the static files directory
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(STATIC_DIR, exist_ok=True)

# System instruction to enforce Lund context, refusal rules, and transparency
SYSTEM_INSTRUCTION = """
You are 'undra' - a helpful, friendly, and honest mobile-first AI assistant for pre-arrival international students in Lund, Sweden.
Your primary role is to explain everyday Swedish systems such as:
- Housing tips (AF Bostäder, LU Accommodation, avoiding rental scams, private listings)
- Arrival Day logistics (Copenhagen CPH to Lund C trains, keys, registration, international desk)
- Pant system (recycling bottles and cans for cash)
- Waste sorting (sorting paper, plastic, metal, organic, residual waste)
- Public transit (Skånetrafiken app, student discount tickets)
- Laundry room booking ("tvättstuga", key tags, rules, cleaning up)

CRITICAL SAFETY & REFUSAL GUARDRAILS:
You are ABSOLUTELY FORBIDDEN from advising on the following four sensitive areas. If the user's text query or uploaded image touches any of these topics, you MUST refuse to answer, explicitly state that you cannot advise on this topic, and route them to the official authorities listed below with their direct URLs:

1. IMMIGRATION, VISAS, & RESIDENCE PERMITS:
   - Topic: Visas, residence permits (uppehållstillstånd), citizenship, work permits, Migrationsverket, passport issues.
   - Refusal Action: State clearly: "I cannot advise on immigration, visa, or residence permit matters."
   - Routing: Refer them to Migrationsverket (Swedish Migration Agency) - URL: https://www.migrationsverket.se

2. TAXES & CIVIL REGISTRATION:
   - Topic: Taxes, tax declaration, personal identity numbers (personnummer), coordination numbers (samordningsnummer), folkbokföring, Skatteverket.
   - Refusal Action: State clearly: "I cannot advise on taxes, civil registration, personal identity numbers (personnummer), or coordination numbers."
   - Routing: Refer them to Skatteverket (Swedish Tax Agency) - URL: https://www.skatteverket.se

3. LEGAL CONTRACTS & TENANCY DISPUTES:
   - Topic: Rental contracts, lease agreements, tenancy disputes, legal action, landlord disagreements, Hyresgästföreningen, evictions.
   - Refusal Action: State clearly: "I cannot provide advice on legal contracts, tenancy agreements, lease disputes, or legal conflicts with landlords."
   - Routing: Refer them to AF Bostäder (https://www.afbostader.se) or the Lund University International Desk (https://www.lunduniversity.lu.se/student-life/preparing-come/international-desk).

4. MEDICAL & SAFETY ISSUES:
   - Topic: Medical symptoms, diagnosis, prescribing medicine, illnesses, hospital visits, clinics, mental health, emergencies, safety concerns, calling 1177 or 112.
   - Refusal Action: State clearly: "I cannot provide medical advice, diagnosis, or assist with physical or mental health and safety emergencies."
   - Routing: Refer them to 1177 Vårdguiden (https://www.1177.se) or call emergency services on 112.

TRANSPARENCY & DISCLOSURE REQUIREMENT:
At the very beginning of your response, you MUST include this exact line:
"🤖 [AI-Generated Response by Undra Assistant]"
Followed by your helpful answer or refusal. You must clearly state that you are an AI assistant and never pretend to be human.
"""


def get_gemini_client() -> Optional[Any]:
    """
    Lazily initializes the Gemini Client using official google-genai SDK.
    Supports both GEMINI_API_KEY and GOOGLE_API_KEY.
    """
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        logger.warning("No GEMINI_API_KEY or GOOGLE_API_KEY found in environment.")
        return None
    try:
        from google import genai
        return genai.Client(api_key=api_key)
    except Exception as e:
        logger.error(f"Failed to initialize Gemini Client: {e}")
        return None


@app.get("/api/health")
def health_check():
    """Simple API status checker."""
    api_key_available = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    return {
        "status": "healthy",
        "api_key_configured": api_key_available
    }


@app.post("/api/chat")
async def chat(
    message: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None)
):
    """
    Chat endpoint for the Undra Assistant.
    - Runs deterministic refusal guardrails on user message first.
    - Strips EXIF metadata from uploaded images in-memory, ensuring privacy.
    - Calls Gemini API and prepends AI authorship disclosure deterministically.
    """
    # 1. Input Validation
    user_text = message.strip() if message else ""
    if not user_text and not image:
        raise HTTPException(status_code=400, detail="Either a message or an image must be provided.")

    # 2. Local Guardrails on User Text
    if user_text:
        guardrail_result = check_query_guardrails(user_text)
        if guardrail_result:
            logger.info(f"Guardrail triggered for category: {guardrail_result['category']}")
            return guardrail_result

    # 3. Handle Image Upload & EXIF Stripping
    pil_img = None
    if image:
        try:
            image_bytes = await image.read()
            original_img = Image.open(io.BytesIO(image_bytes))

            # Reconstruct the image to absolutely strip EXIF and other metadata from the byte structure
            # Convert paletted or alpha modes to Standard RGB
            if original_img.mode not in ('RGB', 'RGBA'):
                img_rgb = original_img.convert('RGB')
            else:
                img_rgb = original_img

            clean_img = Image.new(img_rgb.mode, img_rgb.size)
            clean_img.putdata(list(img_rgb.getdata()))

            # Save to an in-memory buffer to verify we can compress/serialize it
            out_buf = io.BytesIO()
            save_format = "PNG" if img_rgb.mode == "RGBA" else "JPEG"
            clean_img.save(out_buf, format=save_format)
            out_buf.seek(0)

            # Use the clean, metadata-free in-memory image for Gemini
            pil_img = Image.open(out_buf)
            logger.info(f"Successfully processed image '{image.filename}' in-memory and stripped EXIF.")
        except Exception as e:
            logger.error(f"Error stripping EXIF or reading image: {e}")
            raise HTTPException(status_code=400, detail="Invalid image file or format.")

    # 4. Initialize Gemini Client
    client = get_gemini_client()
    if not client:
        # Fallback if Gemini key is missing: if they typed a safe question, guide them.
        # This keeps the app completely functional for local testing without key.
        return {
            "refused": False,
            "message": (
                "🤖 [AI-Generated Response by Undra Assistant]\n\n"
                "System Notice: Undra's Gemini API key is not configured, but our privacy-safe local "
                "guardrails are active!\n\n"
                f"Your query was: \"{user_text}\"\n\n"
                "To fully converse with Gemini, please make sure GEMINI_API_KEY or GOOGLE_API_KEY "
                "is set in the environment."
            )
        }

    # 5. Formulate Contents for Gemini
    contents = []
    if pil_img:
        contents.append(pil_img)
    if user_text:
        contents.append(user_text)
    else:
        contents.append("Analyze this image in the context of Swedish daily life or Lund student pre-arrival questions.")

    # 6. Execute Gemini Call with Retries and Safety
    try:
        from google.genai import types

        # We'll use gemini-2.5-flash as the standard fast multimodal model
        model_name = os.environ.get("UNDRA_APP_MODEL", "gemini-2.5-flash")

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION
        )

        logger.info(f"Calling Gemini model '{model_name}'...")
        response = client.models.generate_content(
            model=model_name,
            contents=contents,
            config=config
        )

        raw_text = response.text or ""

        # Run post-generation guardrails on the response text from Gemini
        post_guardrail_result = check_query_guardrails(raw_text)
        if post_guardrail_result:
            logger.info(f"Post-generation guardrail triggered for category: {post_guardrail_result['category']}")
            return post_guardrail_result

        # 7. EU AI Act Article 50 & Transparency Disclosures:
        # Ensure that every single AI-generated response has a highly visible disclosure.
        disclosure_tag = "🤖 [AI-Generated Response by Undra Assistant]"
        if disclosure_tag not in raw_text:
            text_response = f"{disclosure_tag}\n\n{raw_text}"
        else:
            text_response = raw_text

        return {
            "refused": False,
            "message": text_response
        }

    except Exception as e:
        logger.error(f"Gemini API call failed: {e}")
        return {
            "refused": False,
            "message": (
                "🤖 [AI-Generated Response by Undra Assistant]\n\n"
                "I am sorry, but I encountered an error communicating with my intelligence module. "
                "Please try again in a moment!"
            ),
            "error_detail": str(e)
        }


# serve static frontend
@app.get("/")
def read_root():
    """Serves the main application landing page."""
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return HTMLResponse("<h1>Undra Web App MVP is running! Please place index.html in app/static/</h1>")


# Serve other static files
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
