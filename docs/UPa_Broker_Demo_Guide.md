# Demo broker — simple walkthrough

Open **http://127.0.0.1:8085/**.

Before starting: the broker, Juan, Jerome, and the caretaker should be **Online**. Enable **Show deliveries and ACKs**. Leave the optional fourth subscriber off.

If the control room is closed, run this from PowerShell:

```powershell
Set-Location 'C:\Users\Client\Desktop\UPa'
.\scripts\pyrun.bat -m pubsub.dashboard --port 8085 --broker-port 15625 --data-dir .presentation-demo
```

Click **Start broker**, then **Start** on the three subscriber cards. Wait for Online. Keep this window running.

## 1. Introduce the system

Point to the architecture diagram, then the dashboard.

**Say:** “Two publishers—the manager's office and maintenance desk—send messages through a Python broker. Juan, Jerome, and the caretaker receive only the topics they subscribed to.”

## 2. Show topic filtering

| Click | Expected result | Say |
|---|---|---|
| Manager's office → **Fire safety inspection - Oct 16, 9:00 AM** | Only Juan receives it. | “Only Juan subscribes to Casa Jaro announcements.” |
| Manager's office → **Welcome to our new tenants!** | Only Jerome receives it. | “Jerome receives Molo announcements instead.” |
| Maintenance desk → **Leaking faucet in shared CR: acknowledged** | Juan and the caretaker receive it. | “A second publisher sends one message to two matching subscribers.” |

## 3. Show offline messages and catch-up

1. Click **Crash** on Juan's card. Wait until he is offline.
2. In Manager's office, click **Scheduled water interruption on Oct 18**.
3. Click **October rent is due on Oct 15**.
4. In Maintenance desk, click **Leaking faucet in shared CR: in progress**.
5. Point to Juan's **3 waiting** messages.
6. Click **Start** on Juan's card.
7. Show the three **Caught up** notices and the queue returning to **0**.

**Say:** “The broker stores messages while Juan is offline. When he reconnects, it delivers everything he missed.”

## 4. Show delivery retry

1. Click **Fault tests → Juan crashes before ACK**. Wait for Juan to be Online.
2. Publish **Pest control this Saturday, 8:00 AM** from Manager's office.
3. Juan receives it, then crashes before confirming receipt.
4. Click **Start** on Juan's card.
5. Show **Duplicate ignored** and the queue returning to **0**.

**Say:** “Without an acknowledgement, the broker sends the message again. Juan recognizes the duplicate, so it is not processed twice. This is at-least-once delivery.”

## 5. Show broker recovery

1. Click **Crash** on Jerome's card.
2. Publish **October rent is due on Oct 10**. Show Jerome's **1 waiting** message.
3. Click **Crash broker**, then **Start broker**.
4. Wait for the broker to recover and clients to reconnect.
5. Click **Start** on Jerome's card. Show his **Caught up** notice.

**Say:** “The queue survives a broker process crash because it is stored in SQLite.”

## 6. Close with the key explanation

**Say:** “Publishers do not need to know their recipients, and subscribers can receive messages later. These are space and time uncoupling from Chapter 6. We use simple topic matching; content-based filtering would inspect message fields and allow more complex rules.”

If asked about the protocol: **PUBLISH → PUBACK → DELIVER → ACK**, using JSON over TCP. SQLite keeps messages, subscriptions, and pending deliveries.

This demo uses a separate broker, so its crash buttons do not stop the public website. Do not reset the demo between disconnecting and reconnecting a subscriber.
