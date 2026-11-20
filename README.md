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

### Step 2: Create your settings file and set your passwords

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

Find these two lines:

```
ADMIN_PASSWORD=change-me
POSTGRES_PASSWORD=change-me
```

Change each `change-me` to a password of your choice:

* `ADMIN_PASSWORD` is the password you log in to the app with.
* `POSTGRES_PASSWORD` protects the database. You never type it anywhere else, so it can be any long random
  string. Use a different password from `ADMIN_PASSWORD`.

Save and close the file. In nano, press **Ctrl+O**, **Enter**, then **Ctrl+X**. Leave everything else as it is.

> ⚠️ Set `POSTGRES_PASSWORD` **before** you start the app for the first time. The database is created with
> this password on the first start, and changing it afterwards stops the app from connecting (see
> [Troubleshooting](#troubleshooting)).

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
| Update SearXNG (the search engines) | `docker compose pull searxng searxng-proxy searxng-tor`, then `docker compose up -d` (see [Keeping SearXNG up to date](#keeping-searxng-up-to-date)) |
| Get the latest version of the app | `git pull`, then `docker compose pull searxng searxng-proxy searxng-tor`, then `docker compose up -d --build` |
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

## Monitor Google with Google Alerts

Google blocks automated searches from SearXNG, but it offers **Google Alerts** instead. A Google Alert can publish
its matches as an RSS feed, and SearXNG Monitor can follow that feed. Its results appear in the same dashboard as
your other searches, with the same new badges, tags, deletion and email/Slack alerts.

**1. Create the alert in Google**

1. Go to <https://www.google.com/alerts> (sign in with a Google account).
2. Type what you want to monitor, e.g. `"Acme Corp"`.
3. Click **Show options**, set **Deliver to** to **RSS feed**, and click **Create Alert**.
4. Click the **RSS icon** next to your new alert. Copy the address from your browser's address bar. It looks like
   `https://www.google.com/alerts/feeds/12345678901234567890/09876543210987654321`.

**2. Add it to SearXNG Monitor**

1. On the dashboard, click **Add Google Alert**, or choose **Google Alert (RSS)** at the top of the
   **New search** form.
2. Give it a **Name** and paste the **feed URL**.
3. Choose how often to check it. Google decides when the feed updates (as things happen, or once a day), so
   every **30–60 minutes** is plenty.
4. Click **Save**. The first check runs within a minute.

You don't type the search terms into SearXNG Monitor; they're read from the feed and shown in the Query column.
A new alert often has **no results at first**. It fills up as Google finds new pages, so a successful check with
0 results is normal.

Google Alerts searches are marked with a **Google** badge on the dashboard. The *Run through proxy* option
doesn't apply to them, because the feed is fetched directly from Google.

## Getting alerts

### Email

Out of the box, email alerts go to a **test inbox** called Mailpit, not to real mailboxes. Mailpit catches every
email the app sends so you can see what the alerts look like, but **nothing is delivered to a real address**. The
app shows this as **"Test inbox"** on the search form, the dashboard and the Settings page.

**Try it with the test inbox:**

1. Edit a search and tick **Email me about new results**.
2. Enter any address, e.g. `me@example.com` (it doesn't need to be real), or set a default address under
   **Settings**.
3. Open **<http://localhost:8025>** to read the alert emails.

**Sending real email**

The app doesn't deliver email itself. It hands each message to an **email server (SMTP server)** you have an
account with, and that server delivers it, a bit like posting a letter at the post office:

```
SearXNG Monitor ──(login)──► email server (e.g. Gmail) ──► the recipient's inbox
```

You can't usefully run your own email server from a laptop: internet providers block it, and anything that gets
through is treated as spam. So borrow an existing account. The easiest is **Gmail**:

1. Use a Gmail account. Ideally create one just for this course.
2. Turn on **2-Step Verification** for it: <https://myaccount.google.com/security>.
3. Create an **App Password** at <https://myaccount.google.com/apppasswords>. Google shows a 16-character
   password. The app logs in with this, not your normal password.
4. Edit these lines in `.env`:

   ```
   EMAIL_HOST=smtp.gmail.com
   EMAIL_PORT=587
   EMAIL_USE_TLS=true
   EMAIL_HOST_USER=youraccount@gmail.com
   EMAIL_HOST_PASSWORD=your16charapppassword
   DEFAULT_FROM_EMAIL=youraccount@gmail.com
   ```

   `DEFAULT_FROM_EMAIL` must be the same Gmail address.
5. Run `docker compose up -d`. In **Settings**, the email status should now say **Real email via
   smtp.gmail.com:587**.
6. Click **Send test email** with your own address, and check your inbox (and spam folder).

Other providers work the same way. Transactional email services such as Brevo, Mailgun or SendGrid give you an
SMTP host, port, username and password to put in the same lines. Work Microsoft 365 accounts often block this
kind of login.

| Problem | What to do |
|---|---|
| Test email fails with *535 … authentication failed* | The App Password is wrong (no spaces), or 2-Step Verification isn't turned on. |
| *Email server … is not reachable* | Check `EMAIL_HOST` and `EMAIL_PORT`. Some networks block outgoing port 587. |
| The email lands in spam | Mark it as "not spam" once and later alerts will arrive normally. |
| Still says **Test inbox** | `EMAIL_HOST` is still `mailpit`. Edit `.env` and run `docker compose up -d`. |

> The App Password is stored in plain text in `.env`. Use a throwaway account, and delete the App Password at
> <https://myaccount.google.com/apppasswords> when the course is over.

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
* **Often blocked:** google, duckduckgo, qwant. They show up as ⚠️ *unresponsive* in the run history. To monitor
  Google, use a [Google Alert](#monitor-google-with-google-alerts) instead.

Results vary by network, so try a few engines and keep the ones that return results for you. Change the engines
ticked for new searches under **Settings**.

---

## Checking that everything works: the Ops page

Click **Ops** in the top menu.

| Tab | Use it to… |
|---|---|
| **Health** | See whether every part of the app is up. All cards should be green. *SearXNG (proxy)* and *Tor* show grey unless you set them up. *SearXNG version* says whether an update is available. |
| **Tasks** | See searches that are running or queued right now, and which searches run next. |
| **History** | Browse every run of every search. Filter by search or status, e.g. *Failed*. |
| **Logs** | Read what the app has been doing: runs, errors, emails sent. Choose **WARNING+** to see only problems. |

---

## Keeping SearXNG up to date

SearXNG gets its results by reading search engines' pages. Those sites change often, which can stop an engine
from working, and SearXNG publishes fixes several times a week. **Update it every week or two**, or whenever
an engine keeps showing up as ⚠️ *unresponsive*.

SearXNG **doesn't update itself**. Once Docker has downloaded it, it keeps using that copy, even after you
restart the app. Automatic updates would need a container with full control of Docker on your computer, so the
app doesn't do that. Instead it tells you when an update is due.

### How you know an update is due

Twice a day the app compares the SearXNG version it's running with the newest one published on
[Docker Hub](https://hub.docker.com/r/searxng/searxng). When yours is **7 days or more** behind:

* The **dashboard** shows a yellow *A SearXNG update is available* box with the commands to run.
* **Ops → Health → SearXNG version** says *Update available*.

The check sends nothing about you or your searches. It only asks Docker Hub for its public list of SearXNG
versions. If the app can't reach Docker Hub, e.g. on an offline network, the Health card shows
*Couldn't check for updates* and nothing else is affected.

### Update SearXNG

In the project folder, run these two commands. They work the same in Terminal, PowerShell and bash:

```bash
docker compose pull searxng searxng-proxy searxng-tor
docker compose up -d
```

The first downloads the newest SearXNG. The second restarts the three SearXNG containers with it. Your searches,
results and settings are kept. Searches pause for about 20 seconds while SearXNG restarts. If Tor is on, the Tor
search engine restarts once Tor has reconnected.

The dashboard box goes away at the next check, within 12 hours.

### Update everything else

* **The app itself:** `git pull`, then the two commands above, using `docker compose up -d --build` in place of
  `docker compose up -d`.
* **Tor and the app's base system** (security fixes): `docker compose build --pull`, then `docker compose up -d`.
  Do this every month or so.

### Change or turn off the check

Set these in `.env`, then run `docker compose up -d`:

* `SEARXNG_UPDATE_MIN_AGE_DAYS=14`: only show the notice when you're at least 14 days behind (default `7`).
* `SEARXNG_UPDATE_CHECK=false`: never check. Remember to update by hand now and then.

---

## Troubleshooting

| Problem | What to do |
|---|---|
| `docker compose` says it can't connect / *daemon not running* | Docker isn't running. **Windows:** start Docker Desktop and wait until it says *Engine running*. **macOS:** start your Docker runtime. **Linux:** run `sudo systemctl start docker`. |
| *The image for the service you're trying to recreate has been removed* or *Pulling beat (searxngmonitor-app)* | You typed `docker-compose` (with a hyphen). That is the old Compose v1, which can't build this app. Answer **N**, then use `docker compose` (with a space). |
| *'compose' is not a docker command* or *unknown shorthand flag: 'd' in -d* | The Compose v2 plugin isn't installed (Linux). Run `sudo apt update`, then `sudo apt install docker-compose-v2` (Docker from Ubuntu's `docker.io` package) or `sudo apt install docker-compose-plugin` (Docker from Docker's own repository). Check with `docker compose version`, then try again. |
| *permission denied* … `docker.sock` (Linux) | Run `sudo usermod -aG docker $USER`, log out and back in, then try again. |
| The browser can't open localhost:8000 | Run `docker compose ps`. If `web` isn't *healthy*, wait a minute. Still not working? Run `docker compose logs web` and look at the last lines. |
| *Port 8000 is already allocated* | Another program is using that port. In `.env`, change `WEB_PORT=8000` to `WEB_PORT=8080` and `APP_BASE_URL` to `http://localhost:8080`, run `docker compose up -d`, and use <http://localhost:8080>. |
| Forgot your password | Change `ADMIN_PASSWORD` in `.env` and run `docker compose up -d`. The new password applies once `web` restarts. |
| `docker compose logs web` shows *password authentication failed for user "searxngmonitor"* | If your `.env` has a `DATABASE_URL=` line (from an older copy of the app), delete that line and run `docker compose up -d`. Otherwise `POSTGRES_PASSWORD` in `.env` was changed after the first start: put the original password back and run `docker compose up -d`. If you don't remember it, start clean with `docker compose down -v`, then `docker compose up -d --build`. ⚠️ This deletes all your searches and results. |
| A run shows **failed** | Hover over *failed* or open **Ops → History**. *"all engines failed"* usually means the engines blocked you, so try other engines or a longer interval. |
| A search finds 0 results | Simplify the query (see above), and check the run history for ⚠️ unresponsive engines. |
| Times look wrong | Times are shown in UTC by default. Set your time zone in `.env`, e.g. `TIME_ZONE=Europe/London` or `TIME_ZONE=America/New_York`, then run `docker compose up -d`. |
| Searches stopped running | Check **Ops → Health**. *Scheduler* and *Celery workers* should be green. If not, run `docker compose up -d`. |
| Want a completely clean start | `docker compose down -v`, then `docker compose up -d --build`. ⚠️ This deletes all your searches and results. |

---

## Optional extras

### Run searches through a proxy

You can send some searches through a proxy, for example to hide your own IP address from the search engines,
spread your traffic, or appear from a different location. Searches without the option ticked still go direct.
Google Alerts aren't affected.

1. Go to **Settings → Proxy**.
2. Tick **Use a proxy…** and fill in:
   * **Type:** `HTTP`, `HTTPS`, `SOCKS5` or `SOCKS5h`. Your proxy provider will tell you which. Use **SOCKS5h**
     for an SSH tunnel, so DNS lookups also go through the proxy. (For Tor, use the bundled Tor instead: see
     [Search Tor (.onion) sites](#search-tor-onion-sites-with-ahmia-and-torch).)
   * **Host** and **Port**, e.g. `proxy.example.net` and `1080`.
   * **Username** and **Password**, only if your proxy needs them. Any characters are fine.
3. Click **Save**. The status shows **Applying…** for about 20 seconds while the proxy search engine restarts,
   then **Active**.
4. Click **Test saved proxy**. It shows your public IP address without and through the proxy. If the two are
   different, the proxy is working. (The test asks <https://api.ipify.org> for your address; change
   `IP_ECHO_URL` in `.env` to use a different service.)
5. Edit a search and tick **Run through proxy**. Runs that used the proxy show 🛡 in **Ops → History**.

The password is never shown again after saving. Leave the box blank to keep it, or tick **Remove the saved
password**.

**Safety:** a search marked *Run through proxy* never runs without the proxy. If the proxy is turned off, still
applying new settings, or unreachable, the run **fails** with an explanation instead of searching from your own
IP address.

| Test result | What to do |
|---|---|
| *The proxy rejected the username or password* | Re-type the password (the box is blank after saving), then Save and test again. |
| *The proxy host … could not be found* | Check the hostname for typos. Enter just the name or IP, without `http://`. |
| *Nothing accepted the connection* | Check the port, and that the proxy is running and allows your IP. |
| Both IP addresses are the same | Your traffic isn't leaving through the proxy. Check the Type, Host and Port with your provider. |

### Search Tor (.onion) sites with Ahmia and Torch

#### What this is

Two search engines can index these .onion sites, and the app can use both if needed:

* **ahmia**: the best-known .onion search engine.
* **torch**: one of the oldest. It often finds different sites from ahmia, so ticking both gives better coverage.

To reach them, the app needs **Tor**.

#### How it works in this app

Tor is **bundled with the app but turned off by default**. Nothing connects to the Tor network until you turn it on.

```text
Search with ahmia/torch ──► Tor search engine ──► tor service ──► Tor network ──► ahmia / torch (.onion)
Every other search      ──► normal search engine (direct, or through your proxy) ──► the internet
```

* **`tor`:** a container that runs Tor. While Tor is off, it runs but makes no connections at all.
* **`searxng-tor`:** a separate copy of SearXNG that sends all its traffic through Tor. It only starts once Tor has
  connected, and stops when you turn Tor off.
* **Which searches use Tor:** any search with **ahmia** or **torch** ticked. The **whole** search runs through Tor,
  including other engines ticked with them (e.g. bing). *Run through proxy* is ignored for these searches.
* **Separate from the proxy:** Tor has nothing to do with **Settings → Proxy**. You don't need to set up a proxy
  to use Tor.
* **What the app fetches:** only the ahmia and torch **search results pages** (titles, links and snippets). It
  never opens the .onion sites in those results.

#### Before you turn it on


* **Results can link to illegal or disturbing material.** Ahmia filters out known abuse content, but no filter is
  complete. Treat .onion links in your results with care. Don't open them in your everyday browser. If you need to
  visit one, use Tor Browser in an isolated environment, such as a dedicated virtual machine, and follow your
  organisation's rules.
* **Expect it to be slower.** A Tor search usually takes 5–15 seconds, compared with 1–3 for a normal one.

#### Turn Tor on

Use either of these:

* **From a search:** tick **ahmia (Tor)** and/or **torch (Tor)**. A yellow box appears; click **Turn on Tor**.
* **From Settings:** go to **Settings → Tor** and click **Turn on Tor**. It takes effect immediately; there is
  no need to click **Save**.

Then:

1. Watch the status in **Settings → Tor**: **Connecting…**, then **Connected**. This usually takes under a
   minute. The first time can take a few minutes, while Tor downloads its list of relays.
2. Click **Test Tor**. It asks <https://check.torproject.org> whether your traffic arrives through Tor. You should
   see *Tor works*, with a Tor exit address that is not your own.

To watch Tor connect, run this in the project folder (press `Ctrl+C` to stop watching):

```bash
docker compose logs -f tor searxng-tor
```

You should see Tor count up to `Bootstrapped 100% (done)`, then `Tor is connected: starting SearXNG (Tor)`.

#### Create a Tor search

1. Create a search as usual (see [Your first monitor](#your-first-monitor)).
2. Under **Engines**, tick **ahmia (Tor)**, **torch (Tor)** or both. Type `(Tor)` in the filter box to find them.
3. Click **Save**, then **Run now** to try it.

Results from both engines are combined, like any other engines. Each result shows which engines found it.

**Query tips:** keep queries short. Both engines are simple keyword search engines, so a single word or a short
phrase works best, e.g. `acme` or `"acme corp"`. Ahmia shows at most 10 results per page.

#### Turn Tor off

Click **Turn off Tor** in **Settings → Tor**. Tor disconnects within a few seconds, and the Tor search engine stops. It will stay on unless you turn it off.

Your Tor searches are kept but **fail** until you turn Tor back on. Each failed run says *Tor is turned off in
Settings*. They never run without Tor. To stop them, pause them on the dashboard, or untick ahmia and torch. 

#### Check on it

* **Settings → Tor** shows the current status: **Off**, **Connecting…** or **Connected**.
* **Ops → Health** has a **Tor (ahmia/torch)** line. It shows **n/a** while Tor is off, which is not an error.
* **Ops → History** shows each run. If ahmia or torch didn't answer, it's listed as ⚠️ *unresponsive*. The run
  still keeps the other engine's results.

#### Troubleshooting Tor

| Problem | What to do |
|---|---|
| Stuck on **Connecting…** for more than 5 minutes | Run `docker compose logs tor`. If it stops at a low `Bootstrapped` percentage, your network is probably blocking Tor. Try another network. Bridges, which help get around blocking, aren't supported. |
| **Test Tor** says *the connection through Tor was dropped* | This happens now and then, especially just after Tor connects. Wait a few seconds and test again. |
| Saving a search says ahmia/torch *need Tor, which is turned off* | Turn Tor on (see above), then save again. |
| A run fails with *Tor isn't ready* | Tor is still connecting. Wait for **Connected**, then click **Run now**. |
| ahmia or torch shows ⚠️ *unresponsive* | Onion sites are sometimes slow or down. It's most common in the first minute or two after Tor connects. If it happens on every run for hours, the engine may be down. |
| Runs time out or say *Skipped: previous run still in progress* | Tor is being slow. Set a longer schedule, e.g. hourly. If needed, raise `SEARXNG_TOR_TIMEOUT` in `.env` (default `60` seconds; keep it above 30), then run `docker compose up -d`. |

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
| `POSTGRES_PASSWORD` | The database password. Set it before the first start and don't change it afterwards. |
| `WEB_PORT` | The port for the app (default `8000`). |
| `TIME_ZONE` | Time zone for displayed times, e.g. `Europe/London`. |
| `DEFAULT_ENGINES` | Engines ticked by default for new searches (also editable under **Settings**). |
| `MIN_INTERVAL_MINUTES` | The shortest schedule allowed (default `5`). |
| `SEARXNG_TOR_TIMEOUT` | Seconds to wait for a Tor (ahmia/torch) search (default `60`). Keep it above 30, the engines' own limit. |
| `SEARXNG_UPDATE_CHECK` | `false` stops the twice-daily check for SearXNG updates (default `true`). See [Keeping SearXNG up to date](#keeping-searxng-up-to-date). |
| `SEARXNG_UPDATE_MIN_AGE_DAYS` | How many days behind SearXNG must be before the update notice shows (default `7`). |
| `EMAIL_*` | Email server details (see [Email](#email)). Leave as `mailpit` for the test inbox. |
| `SLACK_WEBHOOK_URL` | A default Slack webhook (you can also set it under **Settings**). |
| `COMPOSE_PROFILES` | Extras to run: `mail` (test inbox) and `flower` (task dashboard on port 5555). The proxy is set up under **Settings**. |


## License

SearXNG Monitor is released under the [MIT License](LICENSE). It runs [SearXNG](https://github.com/searxng/searxng)
(AGPL-3.0) as a separate, unmodified container. The favicon uses the binoculars icon from
[Bootstrap Icons](https://icons.getbootstrap.com/) (MIT).
