# Ink & Insight

Ink & Insight is a Flask + SQLite assignment operations platform built for students, workers, and one owner approval panel.

- Students can submit assignment requests, provide delivery/contact details, choose a budget range, wait for the owner to set the final agreed price, pay securely, track order status, and message the assigned worker.
- Workers can register themselves, but they cannot log in until the owner approves them.
- The owner uses a simple dashboard to approve or reject new worker registrations.

## What is included

- Student-facing landing page with a dark/light mode toggle
- Simpler intake form for handwritten work, PowerPoint decks, Word files, and other academic tasks
- Student budget range plus owner-controlled final quote
- Order tracking page with payment state, status timeline, and message thread
- Worker login, worker registration, and owner approval flow
- Assignment claim, status updates, and student approval flow
- Backend SQLite database for students, workers, assignments, messages, status history, and payouts
- Demo payment mode for local testing
- Razorpay-ready order creation and signature verification flow for live payments

## Local setup

1. Create and activate a virtual environment if you want an isolated install.
2. Install dependencies:

```powershell
py -m pip install -r requirements.txt
```

3. Run the application:

```powershell
py app.py
```

4. Open `http://127.0.0.1:5000`

The SQLite database is created automatically as `assignment_hub.db` in the project root and is seeded with demo workers plus sample assignments.
Set `SEED_DEMO_DATA=false` and `SHOW_DEMO_CREDENTIALS=false` when deploying publicly.

## Deployment

### PythonAnywhere

PythonAnywhere is the easiest fit for this project because it works well with Flask and SQLite.

1. Upload the project or clone it into a folder without spaces, for example `assignment-web`.
2. Create a virtualenv and install dependencies:

```bash
mkvirtualenv --python=/usr/bin/python3.13 assignment-env
workon assignment-env
pip install -r requirements.txt
```

3. Create a new Flask web app from the PythonAnywhere dashboard.
4. Point the virtualenv to your new environment.
5. Copy the example from [deployment/pythonanywhere_wsgi.py.example](C:/Users/sande/Documents/Assignment%20Web/deployment/pythonanywhere_wsgi.py.example) into the PythonAnywhere WSGI config and replace `YOUR_USERNAME`.
6. Reload the web app.

### Render

Render deployment files are already included:

- [render.yaml](C:/Users/sande/Documents/Assignment%20Web/render.yaml)
- [Procfile](C:/Users/sande/Documents/Assignment%20Web/Procfile)

Important:

- The app now supports `DATABASE_PATH`, so Render can store SQLite on a mounted disk.
- `render.yaml` mounts a persistent disk at `/var/data` and points the database to `/var/data/assignment_hub.db`.

Basic Render flow:

1. Push this project to GitHub.
2. In Render, create a new Blueprint or Web Service from the repo.
3. If using the included `render.yaml`, Render will pick up the service config automatically.
4. Set `OWNER_PASSWORD` in Render before going live.
5. If you want live payments, also set `PAYMENT_PROVIDER=razorpay`, `RAZORPAY_KEY_ID`, and `RAZORPAY_KEY_SECRET`.
6. Keep `SHOW_DEMO_CREDENTIALS=false` and `SEED_DEMO_DATA=false` in production so the public site starts clean.

## Demo credentials

- Worker username: `neha.writer`
- Worker password: `demo123`
- Owner username: `owner`
- Owner password: `owner123`

## Payment modes

### Demo mode

This is the default. It lets you test the whole flow without a live gateway:

- Create an order
- Enter a minimum and maximum budget
- Log in as owner and set the final agreed price
- Open the order page
- Start payment
- Confirm the demo payment
- Register a new worker
- Approve that worker from the owner panel
- Log in as a worker and claim the order
- Mark it completed
- Approve it as the student to release payout

### Razorpay mode

Set these environment variables before launching the app:

```powershell
$env:PAYMENT_PROVIDER='razorpay'
$env:RAZORPAY_KEY_ID='your_key_id'
$env:RAZORPAY_KEY_SECRET='your_key_secret'
py app.py
```

The app will then:

- Create a Razorpay order on the server
- Open Razorpay Standard Checkout in the browser
- Verify `razorpay_signature` on the server before marking the assignment as paid

## Notes for production

- Change `SECRET_KEY`
- Replace demo worker passwords
- Replace owner login credentials
- Add webhook handling for payment reconciliation and notifications
- Add file uploads, email delivery, and WhatsApp/SMS integrations
- Expand role controls if you want a separate admin panel beyond the worker dashboard
- Worker dashboard only shows the worker earning amount, while the owner panel shows the full split including commission
- If you want automated live worker payouts, connect a payout product on top of the internal payout ledger

## Project structure

```text
app.py
assignment_web/
  __init__.py
  database.py
  payments.py
  routes.py
  security.py
  services.py
  static/
    app.js
    styles.css
  templates/
    base.html
    index.html
    order_detail.html
    worker_dashboard.html
    worker_login.html
requirements.txt
```
