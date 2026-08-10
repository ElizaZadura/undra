# Undra: Lund Pre-Arrival Student Assistant
## Build with Gemini XPRIZE — Official Submission Package

---

## 1. Project Narrative

### 1.1 The Problem: Navigating Swedish Systems Under Pre-Arrival Anxiety
Every year, approximately 4,000 international students are admitted to Lund University in southern Sweden. For almost all of them, moving to Sweden represents their first time living abroad, and they are immediately confronted with a daunting array of complex, highly structured, and unfamiliar local systems. 

Upon arrival, students must navigate:
1. **The "Pant" Recycling System**: Standard Swedish recycling machines require inserting aluminum cans and PET bottles in exchange for refunds, which is a key part of everyday environmental life but confusing to newcomers.
2. **Standard Waste Sorting (Miljöhus)**: Waste rooms in student corridors and corridors are strictly managed and require sorting waste into multiple specific fractions (e.g., paper packaging, plastic, organic, metal, colored glass, clear glass, residual waste) under threat of fines or community disapproval.
3. **The Unwritten Rules of the Laundry Room (Tvättstuga)**: The Swedish communal laundry room is a notorious cultural flashpoint. Students must understand digital tag bookings, strict timing windows, and standard cleanup etiquette (cleaning lint filters, mopping floors) to avoid neighbor disputes.
4. **Public Transport (Skånetrafiken)**: Obtaining student discounts, registering apps, and understanding how to board regional buses/trains is often a source of arrival stress.
5. **Pre-Arrival Housing Scams & Logistics**: Securing accommodation through AF Bostäder's Novisch lottery or navigating LU Accommodation is a high-stakes, stressful process frequently targeted by rental scammers.

Because these students are navigating these systems **prior to their physical arrival** (Arrival Day for autumn 2026 is scheduled for August 18, 2026), they are highly anxious, situated abroad, and have limited access to physical guides or local support. They face language barriers (as signs and instructions are usually in Swedish) and must absorb multiple dense PDF guides. Traditional search engines often yield generic, conflicting, or outdated information, while general AI chatbots lack local Lund context or, worse, confidently hallucinate legal, tax, or medical advice that could jeopardize a student's legal stay.

---

### 1.2 The Solution: Undra Mobile-First Pre-Arrival Portal & Assistant
**Undra** (meaning "to wonder" in Swedish) is a mobile-first, AI-powered web assistant specifically designed for pre-arrival international students in Lund. It translates the friction of Swedish daily systems into friendly, accessible, and highly accurate guidance.

Undra consists of two primary layers:
1. **Curated Visual Guides**: Static, highly optimized mobile-first guides explaining core pre-arrival topics:
   - **Housing & Scams**: Demystifying AF Bostäder's novisch lottery, corridor living, and how to spot fraudulent private landlords.
   - **Arrival Day Logistics**: Precise step-by-step travel instructions from Copenhagen Airport (CPH) to Lund Central Station, including border control, train tickets, and key collection at the Lund University International Desk.
   - **The Pant & Sorting Systems**: Clear instructions on how to use supermarket reverse-vending machines and standard recycling rooms.
   - **Communal Laundry Etiquette**: A humorous yet accurate breakdown of the unwritten rules of the *tvättstuga* to prevent cultural misunderstandings.
2. **Interactive Multimodal Q&A Assistant**: An interactive chat interface where students can ask open-ended questions in plain English (or their native language) and upload photos of signs, Swedish letters, recycling symbols, or laundry machine control panels for instant translation and explanation.

---

### 1.3 Target Audience: Admitted International Students
The primary target audience is **pre-arrival international students admitted to Lund University** who are preparing to relocate to Sweden. 

This specific group is characterized by:
- **Anxious Pre-Arrival State**: They are making high-stakes decisions (such as signing housing leases, paying deposits, and arranging travel) from their home countries.
- **Language Barrier**: They do not yet speak Swedish, making official Swedish websites and local signage in photos difficult to interpret.
- **High Mobile Dependency**: They expect to access all essential information on their phones while traveling or packing.
- **Vulnerability to Misinformation**: They are highly vulnerable to rental scams and incorrect advice regarding visa requirements and civil registration.

Undra prioritizes this pre-arrival window, establishing a trustworthy relationship with the student *before* they land on Arrival Day, preparing them to integrate smoothly from day one.

---

### 1.4 Gemini AI Integration Details
Undra is built using an AI-native architecture. The interactive assistant layer is powered by the **Gemini 3.6 Flash** model via the official **Google GenAI SDK** (`google-genai` Python library). The application uses `gemini-3.6-flash` and `gemini-3.1-pro-preview` via the official Google GenAI SDK (`google-genai`).

#### Key Integration Architecture:
- **Fast, Multimodal Reasoning**: We utilize `gemini-3.6-flash` to process both text queries and image uploads. For example, when a user uploads a photo of a laundry booking panel or a recycling label, Gemini's advanced vision capabilities allow it to analyze the Swedish text in the image and explain it within the specific cultural and functional context of Lund student life.
- **Strict System Instructions**: The assistant operates under a comprehensive set of system instructions (injected dynamically via the SDK configuration). These instructions enforce a friendly, honest, and supportive persona representing the "Undra Assistant," define the precise scope of Lund daily systems, and explicitly restrict the assistant from pretending to be human.
- **EU AI Act Transparency Compliance (Article 50)**: In strict compliance with European transparency regulations (which went into force on August 2, 2026), every response generated by the Gemini model is dynamically prepended with a highly visible AI-authorship badge: `🤖 [AI-Generated Response by Undra Assistant]`. The UI and page metadata also clearly declare AI authorship, ensuring users are never misled into thinking they are interacting with a human support agent.
- **Privacy-Safe In-Memory Processing**: To protect user privacy (especially since students may upload photos of official documents or tenancy agreements containing PII), the application strips all EXIF and metadata from uploaded images in-memory upon receipt. Uploaded images are strictly kept in-memory as ephemeral buffers during the API call and are **never written to persistent disk or storage**, adhering to strict GDPR-compliant principles.

---

### 1.5 Refusal Guardrails Design: Deterministic "Refuse and Route"
A core innovation of Undra is its robust safety and legal guardrail system. In student advisory contexts, the most dangerous failure mode is an AI hallucinating or giving incorrect advice on legal, tax, medical, or immigration matters. A confident but incorrect answer about a visa status or tax obligation can result in deportation or financial penalties for a student.

To address this, Undra implements a deterministic, multi-layered **"Refuse and Route"** design rather than relying solely on soft system-prompt instruction.

#### The Four Restricted Categories (CHARTER §3.3):
1. **Immigration & Visas**: Visa applications, residence permits (*uppehållstillstånd*), work permits, citizenship, or asylum.
2. **Taxes & Civil Registration**: Swedish tax registration (*Skatteverket*), obtaining a personal identity number (*personnummer*) or coordination number (*samordningsnummer*), and population registration (*folkbokföring*).
3. **Legal Contracts & Tenancy Disputes**: Official lease disputes with landlords, security deposit conflicts, or legal actions.
4. **Medical & Safety Concerns**: Medical diagnoses, prescribing medication, clinical advice, or active physical and mental health emergencies.

#### Deterministic Guardrail Implementation:
Our guardrail architecture consists of two primary defensive lines:
- **Pre-Generation Local Guardrail**: Before any data is sent to the Gemini API, the user's text query is scanned locally using highly optimized regular expressions matching key Swedish and English regulatory terminology (e.g., *Migrationsverket*, *Skatteverket*, *personnummer*, *visas*, *deposit dispute*, *fever*, *112*). If a pattern matches, the query is blocked **locally and deterministically**.
- **Post-Generation Guardrail**: In cases where a user attempts to bypass the input scanner using adversarial prompt injection or image-only queries, the raw text response returned by the Gemini API is passed through the same keyword and pattern scanner. If Gemini attempts to generate advice on a restricted topic, the response is blocked **post-generation**.
- **The "Route" UX**: When a guardrail is triggered (either pre- or post-generation), the application bypasses standard chat output and returns a structured refusal card. This card:
  1. Gracefully explains that Undra is safely and legally prohibited from advising on this highly sensitive topic.
  2. Provides **direct, official link buttons** routing the student to the exact Swedish authority responsible for that area:
     - **Migrationsverket** (Swedish Migration Agency) for visa and permit queries.
     - **Skatteverket** (Swedish Tax Agency) for tax and civil registration.
     - **AF Bostäder & Lund University International Desk** for housing and contract assistance.
     - **1177 Vårdguiden** and **112 Emergency Services** for medical and safety concerns.

This deterministic approach ensures that Undra behaves with perfect safety and utility, acknowledging its limitations and pointing students to the safest possible official resources.

---

## 2. Financial Summary

### 2.1 Total Expenditure to Date
The development, hosting, and operations of Undra have been kept highly cost-efficient. Total recorded expenditure to date in the ledger is $6.89 USD for Gemini API LLM usage. All other operational spend categories are $0.00 USD recorded.

While our environment is configured with a strict sixty-dollar USD spend cap, this figure is a configured safety cap and budget ceiling, not actual expenditure.

The following table provides a detailed breakdown of the actual recorded costs from the ledger:

| Category | Description | Cost (USD) | Funding Source |
|---|---|---|---|
| **Gemini API LLM Usage** | High-accuracy multimodal reasoning and planning queries | $6.89 USD | Prepaid Balance (Project `undra-504613`) |
| **Other operational expenses** | Web application hosting, domain registration, and infrastructure costs | $0.00 USD | GCP Free Trial Credit / Personal out-of-pocket |
| **Total recorded spend** | Combined actual recorded expenditure to date | $6.89 USD | Human Personal Funding |

---

### 2.2 Arms-Length Revenue Breakdown (May – August 2026)
Undra did not generate any arms-length revenue within the hackathon window. Note that project operations commenced on 2026-08-06. The periods of May of 2026, June of 2026, and July of 2026 precede the start of the project (the project did not exist prior to 2026-08-06, so no activity or revenue existed for those months). For August 2026, recorded revenue is $0.00 USD.

The monthly breakdown is detailed below:

| Month | Arms-Length Revenue (USD) | Explanation |
|---|---|---|
| **May (pre-project)** | $0.00 | Precedes the start of the project on 2026-08-06 (no activity or revenue existed). |
| **June (pre-project)** | $0.00 | Precedes the start of the project on 2026-08-06 (no activity or revenue existed). |
| **July (pre-project)** | $0.00 | Precedes the start of the project on 2026-08-06 (no activity or revenue existed). |
| **August 2026** | $0.00 | Recorded revenue is $0.00 USD. Project operations commenced on 2026-08-06. |

#### Product & Business Rationale for Zero Revenue:
Our choice to keep the platform entirely free of charge is a deliberate and conscious product decision:
1. **Target Audience Constraints**: Our target audience consists of pre-arrival international students who are already facing massive moving expenses (tuition fees, visa fees, flights, and rent deposits). Introducing a paywall or subscription fee during this highly anxious pre-arrival phase would create a significant barrier to entry, defeating the educational mission of the platform.
2. **Timing Alignment**: Lund University's official Arrival Day is **August 18, 2026**, which sits precisely one day *after* the XPRIZE submission deadline of August 17, 2026. Because the target student demographic does not land in Sweden and encounter daily physical systems (such as recycling rooms and laundry tags) until Arrival Day itself, no organic transaction volume could be established inside the submission window.
3. **Establishing Trust**: As a system designed to assist newcomers with unfamiliar foreign regulations, our primary objective is to build absolute trust. Making the MVP completely free and accessible ensures maximum pre-arrival reach and allows us to gather rich, safety-verified conversational data to refine our local guardrail triggers.
4. **Simplification of KYC/Compliance**: Avoiding paywalls allowed us to defer complex payment gateway setups, merchant of record (MoR) compliance, and Know-Your-Customer (KYC) overhead, allowing the team to spend 100% of our engineering budget on safety engineering and deterministic refusal UX.

---

### 2.3 Related-Party Disclosure
To maintain absolute integrity and transparency in our submission, we declare the following:
- **No Related-Party Revenue**: There have been zero transactions, financial exchanges, or revenues generated from related parties, friends, family, or team members.
- **No Artificial Activity**: No dummy transactions or simulated payments were processed to artificially inflate revenue metrics.
- **Personal Funding**: All out-of-pocket costs (such as domain registration and prepaid Gemini credits) were funded directly by the human Operator (Eliza Zadura) as personal development expenditures, with no external corporate sponsors or related-party funding.

---

## 3. Video Script Outline (2-3 Minutes)

This script is structured for a fast-paced, highly engaging 2-to-3-minute video showing a split-screen layout: one side featuring a presenter or clean slides, and the other displaying the mobile web interface running in real-time.

### 3.1 Overview & Structure

*   **Total Duration**: 2 minutes, 30 seconds (150 seconds)
*   **Tone**: Practical, empathetic, tech-forward, and highly focused on safety.
*   **Characters/Visuals**: Narrator (voiceover or face-to-camera), mobile screen capture of the Undra Web App in a smartphone simulator, and mock photos of Swedish systems.

---

### 3.2 Scene-by-Scene Script

#### Scene 1: The Pre-Arrival Challenge (0:00 - 0:30)
*   **Visual**: A split screen. On the left, a video of Lund University's historic main building. On the right, stock video of an international student packing a suitcase while looking stressed at their laptop, surrounded by open browser tabs of official Swedish authorities.
*   **Narrator (Voiceover)**:
    > "Every autumn, four thousand international students move to Lund, Sweden. They arrive excited, but also completely overwhelmed. Before they even step off the plane, they are hit with unfamiliar everyday systems: complex multi-fraction recycling, the Swedish 'pant' machine refund system, digital transit apps, and the unwritten, highly strict social rules of the communal laundry room—the 'tvättstuga'. To make matters worse, most guides are in Swedish, leaving students anxious and vulnerable to rental scams abroad. Meet Undra: your mobile-first pre-arrival assistant."

#### Scene 2: Introducing Undra & Pre-Arrival Guides (0:30 - 1:00)
*   **Visual**: Screen recording of a smartphone opening `https://undra.nu`. The user scrolls through the sleek, fast, mobile-responsive UI. They click on the "Laundry Booking" visual guide, showing simple English explanations of tags and cleanup rules.
*   **Narrator (Voiceover)**:
    > "Undra translates local complexity into friendly, interactive student guides. Built explicitly for the pre-arrival window, Undra provides mobile-optimized resources explaining housing lotteries, Arrival Day logistics from Copenhagen Airport, and transit discounts, ensuring students feel integrated before they even pack their bags."

#### Scene 3: Multimodal Gemini AI Q&A (1:00 - 1:30)
*   **Visual**: In the chat simulator, the user uploads a photo of a Swedish laundry room control panel with complex cycles written in Swedish. They type: *"How do I run a quick wash?"* 
*   **Visual (Action)**: The screen shows Undra's in-memory processing (metadata is stripped instantly for privacy), and Gemini 3.6 Flash processes the query. Within seconds, a response appears with the `🤖 [AI-Generated Response by Undra Assistant]` tag, giving a perfect English translation of the panel buttons and step-by-step instructions.
*   **Narrator (Voiceover)**:
    > "But what happens when you encounter a physical sign or a notice you can't read? Undra is powered by Gemini 3.6 Flash via the Google GenAI SDK. Students can upload photos of local signage, Swedish recycling bins, or appliance screens. Undra strips the EXIF metadata in-memory for absolute privacy, analyzes the image, and provides context-rich English answers in real-time."

#### Scene 4: Deterministic Refuse-and-Route Safety (1:30 - 2:10)
*   **Visual**: The user types a query into the chat: *"What are my visa options if my residency permit gets delayed by Migrationsverket?"*
*   **Visual (Action)**: Instantly, instead of a loading spinner or an LLM call, a stylized **Refusal Card** pops up. It displays a clear badge: *"Category: Immigration & Visas"*. It shows a polite explanation of why Undra cannot advise on legal immigration matters, and features a prominent, clickable button: **[Consult Migrationsverket Official Site]**.
*   **Narrator (Voiceover)**:
    > "In student integration, giving incorrect legal, tax, or medical advice is a catastrophic failure mode. Traditional chatbots might hallucinate or guess, risking a student's legal stay. Undra solves this with an AI-native integration: deterministic 'Refuse and Route' guardrails. If a query touches immigration, taxes, contract disputes, or health emergencies, Undra blocks the prompt instantly, both pre- and post-generation. Instead of guessing, we refuse gracefully and route the student directly to official Swedish authorities—like Migrationsverket, Skatteverket, or 1177—with direct, official link buttons."

#### Scene 5: Outro & The Future of Safe Integration (2:10 - 2:30)
*   **Visual**: Zoom out to show both the mobile interface and the beautiful town of Lund. The Undra URL (`undra.nu`) is displayed clearly, along with badges indicating EU AI Act Article 50 compliance and Gemini AI-Native operations.
*   **Narrator (Voiceover)**:
    > "By combining static visual guides, state-of-the-art multimodal Gemini reasoning, and fail-safe deterministic guardrails, Undra represents the future of safe, compliant, and supportive student integration. Built cleanly, operated with absolute integrity, and ready for Arrival Day. This is Undra. Welcome to Lund."
*   **Visual**: Fade to black with credit text: *Built as part of the Build with Gemini XPRIZE, August 2026.*

---

This submission package documents how Undra delivers a safe, AI-native, highly specialized educational solution to pre-arrival international students in Lund. By prioritizing safety and transparency above speculative revenue, Undra stands as a model for responsible, compliant AI deployment under the EU AI Act.