# Recipe Shorts — free website (Render)

This folder is a ready-to-upload website: it turns a long recipe video into a vertical Short
(finished dish → prep → cooking → final dish → taste → “full recipe on my channel”).

It is set up for **Render’s free plan**. No credit card. It works in Safari on an iPad.

## What the free plan is like

Official pages: [Pricing](https://render.com/pricing) · [Free limits](https://render.com/docs/free) · [Web services](https://render.com/docs/web-services)

- **Cost:** $0. No card needed to sign up or to use the Free compute plan.
- **Computer:** a small one — 512 MB of memory and 0.1 CPU. The site defaults to **720p** Shorts so it fits. 1080p is still in the “More options” menu, but it may fail on long or heavy videos.
- **Sleep:** if nobody visits for 15 minutes the site goes to sleep. The next visit takes about **one minute** to wake up. Render shows a loading page while it starts.
- **Hours:** 750 free hours a month. Because it sleeps, occasional use is comfortably inside that.
- **Downloads:** 5 GB of outgoing traffic a month on the Hobby plan. Stay under that. If you go over and have not added a card, Render pauses the site until next month.
- **Time limit:** a single request can run up to 100 minutes, which is enough to upload a 1–5 minute (or longer) video and wait for the Short. Keep the tab open.
- **Storage:** uploaded files are temporary. They are deleted after a couple of hours, and also whenever the site sleeps or restarts.
- **Speed:** a 6-minute recipe may take several minutes, sometimes closer to 10, because the free computer is small.

## What you need to do (no terminal)

Render builds the site from a GitHub folder. GitHub is also free and does not need a card.
You do **not** need a token if you connect GitHub in Render’s website and click the buttons yourself.

### 1. Make a free Render account

Open [https://dashboard.render.com/register](https://dashboard.render.com/register) and sign up.
You can use your email or your GitHub account. Do **not** add a credit card. Stay on the free **Hobby** plan.

### 2. Put this folder on GitHub

If you do not already have GitHub, sign up at [https://github.com/signup](https://github.com/signup).

1. Click **New repository**. Name it `recipe-shorts`. Public is fine. Click **Create repository**.
2. On the new repo page, click **Add file → Upload files**.
3. Drag **everything inside this `deploy/render` folder** (not the parent `recipe-shorts` project) onto the page:
   `Dockerfile`, `render.yaml`, `requirements.txt`, `app.py`, `.dockerignore`, this `README.md`, and the `recipe_shorts` folder.
4. Click **Commit changes**.

(If you already have GitHub and would rather send a token so this can be uploaded for you, a **classic token** with the `repo` scope from [https://github.com/settings/tokens](https://github.com/settings/tokens) is enough. Do not paste it in a chat if you can help it.)

### 3. Create the free web service on Render

1. In the [Render dashboard](https://dashboard.render.com/), click **New → Web Service**.
2. Choose **Git Provider**, connect GitHub if asked, and select the `recipe-shorts` repo.
3. Check these settings:
   - **Name:** `recipe-shorts` (the public address will look like `https://recipe-shorts.onrender.com`)
   - **Language / Runtime:** Docker
   - **Dockerfile path:** `./Dockerfile`
   - **Instance type / Compute plan:** **Free**
4. Click **Create Web Service** and wait 5–10 minutes for the first build.

You can also click **New → Blueprint** and point it at the same repo — it will read `render.yaml`. Still pick the **Free** plan if asked.

### 4. Open it on the iPad

When the deploy is Live, Render shows a URL such as `https://recipe-shorts.onrender.com`.
Open that in Safari. The first visit after a break may take about a minute.

Upload a recipe video, type the title, tap **Make Short**, keep the page open, then download the Short.

## If you do not want a GitHub account

Render can also run a pre-built Docker image (**New → Web Service → Existing Image**), but that image has to be published somewhere public first (for example Docker Hub). That is a later step. A different free host, SnapDeploy, can also accept a ZIP of this folder without GitHub — see the notes in the project’s deploy folder.

## Local Docker (optional, for developers)

```bash
cd deploy/render
docker build -t recipe-shorts .
docker run --rm -p 7860:10000 --memory=512m recipe-shorts
# then open http://127.0.0.1:7860
```
