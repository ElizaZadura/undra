import io
import os
import logging
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Form, File, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image

from app.guardrails import check_query_guardrails, check_response_guardrails

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

#: Longest edge, in pixels, of the image actually sent to the model. Everything
#: larger is downscaled first. Chosen because the model gains nothing beyond it
#: for the images this product receives — signs, notices, machine panels, which
#: are read from their text — while the phones sending them shoot 50MP.
MAX_IMAGE_EDGE = 1568

#: Refuse absurd uploads with an answer rather than by dying. Cloud Run caps a
#: request at 32MB regardless; this is the point at which we stop before the
#: decoder is handed something pathological.
MAX_IMAGE_BYTES = 20 * 1024 * 1024

#: Pillow's own decompression-bomb ceiling, set explicitly rather than left at
#: whatever the installed version defaults to. Above this, opening raises and
#: the handler returns 400. A container that is killed cannot return anything,
#: which is how this failure mode reached the user as "check your connection".
Image.MAX_IMAGE_PIXELS = 80_000_000

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
            if len(image_bytes) > MAX_IMAGE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=("That image is larger than 20MB. Please send a "
                            "smaller one, or a screenshot of it."))
            original_img = Image.open(io.BytesIO(image_bytes))

            # Decode at reduced scale wherever the format allows it. draft() is
            # a no-op for everything but JPEG; for JPEG it tells libjpeg to
            # decode DCT-scaled, so a 50-megapixel photo is never a
            # 50-megapixel buffer in the first place.
            original_img.draft("RGB", (MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))

            # Convert paletted or alpha modes to Standard RGB
            if original_img.mode not in ('RGB', 'RGBA'):
                img_rgb = original_img.convert('RGB')
            else:
                img_rgb = original_img

            # Downscale before anything else touches the pixels. Gemini derives
            # no benefit from more than this — a laundry booking panel is read
            # from the text, not the sensor — and every megapixel past it costs
            # memory here, latency for the user, and tokens on the call.
            img_rgb.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Image.LANCZOS)

            # Reconstruct the image to absolutely strip EXIF and other metadata
            # from the byte structure. A fresh Image has an empty `info` dict —
            # `.copy()` would carry the original's across, which is the whole
            # thing being prevented.
            #
            # paste(), not putdata(list(getdata())). getdata() yields one Python
            # tuple per pixel and list() materialises all of them: for the
            # 50MP photo a Razr 50 Ultra takes, that is roughly 50 million
            # 64-byte tuples, about 3GB, in a 512MiB container. The instance was
            # OOM-killed mid-request, the connection dropped, and the browser —
            # which cannot tell a dead server from a dead network — told the
            # user to check her connection. Reported from a real phone on
            # 13 August after two failed attempts to record the demo video;
            # reproduced against production at 8160x6120, HTTP 503 in 4.7s.
            # paste() is the same guarantee at C speed and constant overhead.
            clean_img = Image.new(img_rgb.mode, img_rgb.size)
            clean_img.paste(img_rgb)

            # Save to an in-memory buffer to verify we can compress/serialize it
            out_buf = io.BytesIO()
            save_format = "PNG" if img_rgb.mode == "RGBA" else "JPEG"
            clean_img.save(out_buf, format=save_format)
            out_buf.seek(0)

            # Use the clean, metadata-free in-memory image for Gemini
            pil_img = Image.open(out_buf)
            # Deliberately not the filename. A name like "uppehallstillstand_anna.jpg"
            # is personal data, and Cloud Run logs are durable storage — this was the
            # only path by which a user's own words reached it. Size and format are
            # what debugging actually needs.
            logger.info("Successfully processed image in-memory and stripped EXIF "
                        f"({out_buf.getbuffer().nbytes} bytes, {save_format}).")
        except HTTPException:
            # Re-raised as filed. Without this the blanket handler below turns
            # the 413 above into "Invalid image file or format", which sends
            # the user to look for a problem with a file that is fine.
            raise
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

        # The fallback must be a model that exists.
        #
        # This defaulted to gemini-2.5-flash, which Google retired for new
        # users. On 2026-08-07 the deployed service returned 404 for every
        # answerable question while refusals kept working perfectly — refusals
        # are deterministic and never call the model, and neither does
        # /api/health, so every check that was being run stayed green while the
        # product's only real function was broken.
        #
        # cloudbuild.yaml sets UNDRA_APP_MODEL explicitly, so this value is only
        # reached by a deploy that forgets it. That is precisely the deploy that
        # must not resurrect a retired model. Keep this in step with
        # invariants.toml [models]; a deploy-time env var still wins.
        model_name = os.environ.get("UNDRA_APP_MODEL", "gemini-3.6-flash")

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

        # Run post-generation guardrails on the response text from Gemini.
        #
        # check_response_guardrails, not check_query_guardrails. The query
        # patterns match a subject being raised; an answer is allowed to raise
        # subjects — that is what an explanation is. What it may not do is
        # decide something about this reader. Scanning answers with the query
        # patterns refused "1177 Vårdguiden provides medical guidance" for
        # containing the words "medical guidance", which silently undid the
        # 12 August narrowing for exactly the questions it was written for.
        post_guardrail_result = check_response_guardrails(raw_text)
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
