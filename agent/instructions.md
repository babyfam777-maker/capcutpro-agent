# VERCEL IMPLEMENTATION REQUIREMENT

CupCat is a Vercel-based application.

Use Vercel as the primary platform for the web application, AI orchestration, APIs, deployment and infrastructure where appropriate.

Do NOT treat Vercel as merely static hosting.

## STACK

Prefer:

* Next.js
* TypeScript
* Vercel
* Vercel AI SDK for AI interaction
* structured AI outputs
* tool calling
* streaming chat responses where appropriate

Use the existing project stack if it is already correctly configured instead of unnecessarily replacing it.

## AI ARCHITECTURE

The AI editor must operate through tools rather than generating uncontrolled text.

Create tools/functions conceptually similar to:

* analyzeVideo
* transcribeVideo
* analyzeReference
* createEditPlan
* validateEditPlan
* renderVideo
* getRenderStatus
* modifyEditPlan
* exportVideo

The AI chat should be able to call these tools.

Example:

USER:
"Make this into a fast YouTube Short using the reference video's editing style."

AI:

1. inspect source
2. inspect reference
3. analyze both
4. create structured edit plan
5. validate plan
6. submit render job
7. monitor render
8. return preview/download when complete

## IMPORTANT

Do NOT perform long-running video rendering synchronously inside a normal request if the render can exceed the platform's execution limits.

Use an asynchronous job architecture for rendering.

The UI should receive a job ID and display actual render status.

Example state:

ANALYZING
↓
PLANNING
↓
RENDERING
↓
FINALIZING
↓
READY

## VIDEO RENDERING

Keep video rendering modular.

The rendering implementation may use:

* FFmpeg
* Remotion
* or another appropriate rendering service

Choose the implementation based on the existing repository and actual runtime requirements.

Do not force all rendering into Vercel Functions if the workload is inappropriate for them.

If a dedicated rendering worker/service is required, integrate it cleanly with the Vercel application.

## STORAGE

Uploaded videos and rendered videos must use proper object storage.

Do not store large video binaries directly in the application's database.

Store:

* source asset metadata
* reference asset metadata
* render job metadata
* edit plans
* project state
* output metadata

while actual video files live in object storage.

## EDIT PLAN

The edit plan is a first-class object.

The AI should produce structured JSON matching a validated schema.

Example conceptual structure:

{
"format": {
"width": 1080,
"height": 1920,
"fps": 30
},
"clips": [],
"cuts": [],
"transforms": [],
"keyframes": [],
"captions": [],
"overlays": [],
"audio": [],
"sfx": [],
"transitions": []
}

Validate every AI-generated edit plan before sending it to the renderer.

Never blindly execute arbitrary AI-generated commands.

## CHAT

The chat interface is the primary control surface.

Users should be able to say:

"Make it faster."

"More zoom."

"Bigger captions."

"Use the reference style more closely."

"Remove the music."

"Make the hook stronger."

The AI should translate these requests into modifications to the existing edit plan.

Do NOT unnecessarily regenerate the entire project.

## DEPLOYMENT

The project must be deployable to Vercel.

Before declaring the implementation complete:

* run TypeScript checks
* run lint
* run tests
* build the production application
* verify environment variables
* verify upload flow
* verify AI calls
* verify render-job creation
* verify render status updates
* verify final video retrieval

Do not claim a feature works unless it has actually been tested.

## PRODUCT RULE

CupCat must remain an actual AI video editor.

Do not reduce it to:

"ChatGPT + a few FFmpeg filters."

The AI must understand footage, create an edit strategy, generate a structured edit plan, execute it through real video-processing infrastructure, and allow conversational revisions.

The target experience is:

CHATGPT × CAPCUT PRO × AI VIDEO EDITOR

with Vercel providing the application and AI orchestration layer.