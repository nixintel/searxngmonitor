# SearXNG Monitor

SearXNG Monitor watches search engines for keywords and phrases you care about. You save a search once, and the
app re-runs it on a schedule. Results it hasn't seen before are flagged as **new**, and it can alert you by email
or Slack.

You don't need to know anything about how the app is built. You only need Docker and a web browser.

---

## Quick start

### What you need

* **Docker**, with the Compose plugin:
  * **Windows:** install **Docker Desktop** from <https://www.docker.com/products/docker-desktop/> and accept the
    default WSL 2 option during install. Start Docker Desktop before you begin.
  * **macOS:** use the Docker runtime you already have (for example OrbStack, Colima or Docker Desktop) and make
    sure it's running.
  * **Linux:** install Docker Engine and the Compose plugin by following
    <https://docs.docker.com/engine/install/>. Then add yourself to the `docker` group so you don't need `sudo`:
    run `sudo usermod -aG docker $USER`, then log out and back in.
* **Git**, to download the app. Check with `git --version`. If it's missing:
  * **Windows:** install it from <https://git-scm.com/download/win> (the default options are fine), then open a
    new PowerShell window.
  * **macOS:** run `git --version` in Terminal and accept the offer to install the developer tools.
  * **Linux:** install it with your package manager, e.g. `sudo apt install git`.
* About **2 GB of free disk space** and a working internet connection.

To check that Docker is ready, open a terminal (on Windows: **Start → PowerShell**; on macOS: **Terminal**) and run:

```
docker compose version
```

You should see a version number. If you get an error, make sure Docker is running (on Windows, start Docker
Desktop and wait until it says *Engine running*; on Linux, run `sudo systemctl start docker`) and try again.

### Step 1: Download the app

In your terminal, run these two commands. They work the same on Windows, macOS and Linux:

```
git clone https://github.com/nixintel/searxngmonitor.git
cd searxngmonitor
```

This creates a `searxngmonitor` folder in your home folder and moves you into it. **Run every command from now
on inside this folder.** If you close the terminal, open a new one and run `cd searxngmonitor` first.

### Step 2: Create your settings file and set your password

Copy the example settings file, then open it in a text editor.

**Windows (PowerShell):**

```powershell
Copy-Item .env.example .env
notepad .env
```

**macOS / Linux:**

```bash
cp .env.example .env
nano .env
```

Find this line:

```
ADMIN_PASSWORD=change-me
```

Change `change-me` to a password of your choice, then save and close the file. In nano, press
**Ctrl+O**, **Enter**, then **Ctrl+X**. Leave everything else as it is.

### Step 3: Start the app

```
docker compose up -d --build
```

The first start takes **3–5 minutes** because Docker downloads and builds everything. Later starts take
seconds.

### Step 4: Check that it's running

```
docker compose ps
```

Wait until the `web` line shows **(healthy)**, which takes up to a minute after Step 3 finishes. Run the command again
to refresh.

### Step 5: Log in

Open **<http://localhost:8000>** in your browser and log in with:

* **Username:** `admin`
* **Password:** the one you set in Step 2

You're ready. Go to [Your first monitor](#your-first-monitor).

### Everyday commands

Run these from the project folder:

| To… | Run |
|---|---|
| Stop the app (your data is kept) | `docker compose stop` |
| Start it again | `docker compose up -d` |
| Check status | `docker compose ps` |
| Watch what the app is doing | `docker compose logs -f worker` (press **Ctrl+C** to stop watching) |
| Apply changes you made to `.env` | `docker compose up -d` |
| Get the latest version of the app | `git pull`, then `docker compose up -d --build` |
| **Delete everything and start fresh** | `docker compose down -v` ⚠️ removes all searches and results |

Searches keep running on schedule only while the app is running. Stopping Docker or shutting down your computer
pauses them. Any overdue search runs once as soon as the app starts again.

---

## Your first monitor

### 1. Create a search

Click **New search** in the top menu and fill in the form:

| Field | What to enter |
|---|---|
| **Name** | A label for your list, e.g. `Acme Corp mentions`. |
| **Query** | What to search for. Put exact phrases in quotes: `"Acme Corp"`. See [Writing good queries](#writing-good-queries). |
| **Engines** | Which search engines to ask. The defaults (brave, bing, yahoo, mwmbl) are a good start. Use the filter box to find others. |
| **Run every / Unit** | How often to search, e.g. `6` `Hours`. The minimum is 5 minutes. |
| **Time range** | Optionally limit results to the past day, week, month or year. |
| **Max pages** | How many pages of results to fetch per engine. `1` is usually enough. More pages means more results but slower runs. |
| **Baseline first run** | Tick this if you only want to hear about results that appear **after today**. The first batch is stored as already seen. |
| **Email me / Post to Slack** | Optional alerts. See [Getting alerts](#getting-alerts). |

Click **Save**. The search runs for the first time **within a minute**. You don't need to wait for the schedule.

### 2. Read the dashboard

The **Dashboard** lists all your searches:

* **Last search / Next search:** when the search last ran and when it will run next.
* **Last status:** <kbd>success</kbd>, <kbd>running</kbd>, <kbd>queued</kbd> or <kbd>failed</kbd>. Hover over
  *failed* to see why.
* **Results:** a yellow **N new** badge for unseen results, a blue tag badge for results you've tagged, and the
  total.
* **Buttons:** ▶ run now, ⏸ pause/resume, ✏️ edit, 🗑 delete the search.

The page refreshes itself every 30 seconds. The **N new** badge in the top menu is your total of unseen results
across all searches.

### 3. Review the results

Click a search's name to open it. Results are split into tabs:

| Tab | Shows |
|---|---|
| **All** | Every result found so far (except deleted ones). |
| **New** | Results you haven't reviewed yet. They have a yellow edge. |
| **Tagged** | Results you've marked as interesting. Click a tag at the top to filter by it. |
| **Deleted** | Results you've removed. Use **Restore** to bring one back. |

On each result you can:

* Click **new ✓** to mark it as seen.
* Click **☆** to tag it as `interesting`.
* Type into **+ tag** and press **Enter** to add your own tags, e.g. `lead`, `follow-up` or `phishing`. Separate
  several tags with commas.
* Click **×** on a tag to remove it.
* Click **🗑** to delete it. Deleted results **won't come back** as new on later runs. Use this to clear out
  noise.

To act on many results at once, tick the boxes on the left, or **All on page**, then use the bar above the list:
**Mark seen**, **Tag**, **Untag** or **Delete**.

The **Run history** panel on the right shows each run: when it ran, how many results it found (*returned / new*),
and any errors. A ⚠️ icon means some engines didn't respond. Hover over it to see which.

### 4. Run a search right now

Click **Run now** on the search page, or ▶ on the dashboard. The run appears in the run history within about 10
seconds. It doesn't change the regular schedule.

---

## Getting alerts

### Email

The app includes a **test inbox** called Mailpit. It catches every email the app sends, so nothing goes to a real
mailbox.

1. Edit a search and tick **Email me about new results**.
2. Enter any address, e.g. `me@example.com`, or set a default address under **Settings**.
3. Open **<http://localhost:8025>** to read the alert emails.

To check it's working, go to **Settings** and click **Send test email**.

If the new-search form shows a yellow **"Email alerts are not available"** warning, the email server can't be
reached. Check that `COMPOSE_PROFILES=mail` is in your `.env` file, then run `docker compose up -d`.

<details>
<summary>Sending real email instead (e.g. through Gmail or your organisation's mail server)</summary>

Edit these lines in `.env`, then run `docker compose up -d`:

```
EMAIL_HOST=smtp.example.com
EMAIL_PORT=587
EMAIL_HOST_USER=your-username
EMAIL_HOST_PASSWORD=your-password
EMAIL_USE_TLS=true
DEFAULT_FROM_EMAIL=monitor@example.com
```

For Gmail, use `smtp.gmail.com` with an **App Password**, not your normal password. Then use **Settings → Send test
email** to check it.
</details>

### Slack

The app posts to Slack using an **incoming webhook**. This is a private web address that Slack creates for one
channel; anything sent to it appears in that channel as a message.

**Create a webhook (about 2 minutes):**

1. Go to <https://api.slack.com/apps> and click **Create New App** → **From scratch**. Give it a name (e.g.
   *SearXNG Monitor*) and pick your workspace.
2. In the menu on the left, open **Incoming Webhooks** and switch it **On**.
3. Click **Add New Webhook to Workspace**, choose a channel and click **Allow**. Your workspace admin may need to
   approve this.
4. Copy the **Webhook URL**. It starts with `https://hooks.slack.com/services/`.

**Connect it to the app:**

1. In SearXNG Monitor, go to **Settings**, paste the URL into **Default Slack webhook URL** and click **Save**.
2. Click **Send test message** and check the message appears in your channel.
3. Edit a search and tick **Post new results to Slack**.

Each alert lists up to 15 new results with links, plus a link back to the search in the app. To send a particular
search to a different channel, paste a different webhook URL into that search's own **Slack webhook URL** field.

> **Keep the webhook URL secret.** Anyone who has it can post messages to your channel. If it leaks, delete the
> webhook on the Slack app page and create a new one.

---

## Writing good queries

* **Use quotes for exact phrases:** `"Acme Corp"` finds that phrase. `Acme Corp` finds pages with both words
  anywhere.
* **Combine terms:** `"Acme Corp" breach` or `"Acme Corp" (leak OR dump)`. Engines differ in which operators they
  support, so check a few runs to see what works.
* **Narrow to a site:** `"Acme Corp" site:pastebin.com` (supported by most engines).
* **Watch for variations:** create separate searches for alternative spellings, usernames, email domains or
  product names rather than one huge query.
* **Too much noise?** Add more specific words, set a **Time range** such as *Past week*, and delete irrelevant
  results so they don't come back.
* **Nothing found?** Remove quotes or extra words, add more engines, and check the run history for engine errors.

### Choosing engines

Some engines block automated searches. From most networks we've seen:

* **Work well:** bing, yahoo, mwmbl, yandex.
* **Partly working:** brave. Many of its results currently arrive as broken links, which the app skips.
* **Often blocked:** google, duckduckgo, qwant. They show up as ⚠️ *unresponsive* in the run history.

Results vary by network, so try a few engines and keep the ones that return results for you. Change the engines
ticked for new searches under **Settings**.

---

## Checking that everything works: the Ops page

Click **Ops** in the top menu.

| Tab | Use it to… |
|---|---|
| **Health** | See whether every part of the app is up. All cards should be green. *SearXNG (proxy)* shows grey unless you set up a proxy. |
| **Tasks** | See searches that are running or queued right now, and which searches run next. |
| **History** | Browse every run of every search. Filter by search or status, e.g. *Failed*. |
| **Logs** | Read what the app has been doing: runs, errors, emails sent. Choose **WARNING+** to see only problems. |

---

## Troubleshooting

| Problem | What to do |
|---|---|
| `docker compose` says it can't connect / *daemon not running* | Docker isn't running. **Windows:** start Docker Desktop and wait until it says *Engine running*. **macOS:** start your Docker runtime. **Linux:** run `sudo systemctl start docker`. |
| *permission denied* … `docker.sock` (Linux) | Run `sudo usermod -aG docker $USER`, log out and back in, then try again. |
| The browser can't open localhost:8000 | Run `docker compose ps`. If `web` isn't *healthy*, wait a minute. Still not working? Run `docker compose logs web` and look at the last lines. |
| *Port 8000 is already allocated* | Another program is using that port. In `.env`, change `WEB_PORT=8000` to `WEB_PORT=8080` and `APP_BASE_URL` to `http://localhost:8080`, run `docker compose up -d`, and use <http://localhost:8080>. |
| Forgot your password | Change `ADMIN_PASSWORD` in `.env` and run `docker compose up -d`. The new password applies once `web` restarts. |
| A run shows **failed** | Hover over *failed* or open **Ops → History**. *"all engines failed"* usually means the engines blocked you, so try other engines or a longer interval. |
| A search finds 0 results | Simplify the query (see above), and check the run history for ⚠️ unresponsive engines. |
| Times look wrong | Times are shown in UTC by default. Set your time zone in `.env`, e.g. `TIME_ZONE=Europe/London` or `TIME_ZONE=America/New_York`, then run `docker compose up -d`. |
| Searches stopped running | Check **Ops → Health**. *Scheduler* and *Celery workers* should be green. If not, run `docker compose up -d`. |
| Want a completely clean start | `docker compose down -v`, then `docker compose up -d --build`. ⚠️ This deletes all your searches and results. |

---

## Optional extras

### Run searches through a proxy

You can send some searches through a proxy, for example to spread traffic or appear from a different location.
Searches without the option ticked still go direct.

1. Edit `.env`:

   ```
   COMPOSE_PROFILES=mail,proxy
   SEARXNG_PROXY_ENABLED=true
   PROXY_URL=socks5h://user:password@proxy.example.net:1080
   ```

   `PROXY_URL` can be `http://…`, `socks5://…` or `socks5h://…`. If the password contains `#` or `&`, replace them
   with `%23` and `%26`.

2. Run `docker compose up -d`.
3. Check that **Ops → Health → SearXNG (proxy)** is green.
4. Edit a search and tick **Run through proxy**. Runs that used the proxy show 🛡 in **Ops → History**.

### Use the API from scripts

Everything in the app can also be done from scripts. Browse and try the API at **<http://localhost:8000/api/docs/>**
while logged in. From a terminal:

```bash
# 1. Get a token (replace YOUR_PASSWORD)
curl -X POST http://localhost:8000/api/v1/auth/token/ -d username=admin -d password=YOUR_PASSWORD

# 2. Use it (replace YOUR_TOKEN)
curl -H "Authorization: Token YOUR_TOKEN" http://localhost:8000/api/v1/searches/
curl -H "Authorization: Token YOUR_TOKEN" "http://localhost:8000/api/v1/results/?view=new"
curl -H "Authorization: Token YOUR_TOKEN" "http://localhost:8000/api/v1/results/?view=tagged&tag=lead"
```

### Settings you can change in `.env`

Run `docker compose up -d` after any change.

| Setting | What it does |
|---|---|
| `ADMIN_PASSWORD` | Your login password. |
| `WEB_PORT` | The port for the app (default `8000`). |
| `TIME_ZONE` | Time zone for displayed times, e.g. `Europe/London`. |
| `DEFAULT_ENGINES` | Engines ticked by default for new searches (also editable under **Settings**). |
| `MIN_INTERVAL_MINUTES` | The shortest schedule allowed (default `5`). |
| `EMAIL_*` | Email server details (see [Email](#email)). |
| `SLACK_WEBHOOK_URL` | A default Slack webhook (you can also set it under **Settings**). |
| `COMPOSE_PROFILES` | Extras to run: `mail` (test inbox), `proxy`, `flower` (task dashboard on port 5555). |

---

## For instructors

* **Security:** the app is meant to run on a student's own machine. If you host it for others, change every
  `change-me` value in `.env` (generate secrets with `python -c "import secrets; print(secrets.token_urlsafe(48))"`),
  put it behind HTTPS, and set `DJANGO_ALLOWED_HOSTS` / `DJANGO_CSRF_TRUSTED_ORIGINS` to your hostname. Ports 8000
  and 8025 listen on all network interfaces by default.
* **Shared IPs:** a classroom behind one IP address shares the engines' rate limits. Expect more ⚠️ unresponsive
  engines if everyone searches at once. Longer intervals and the proxy option help.
* **Run the test suite:** `docker compose exec web python manage.py test monitor`.
* **More workers for heavy use:** `docker compose up -d --scale worker=3`.
* **Admin interface:** <http://localhost:8000/admin/> gives raw access to all data.
