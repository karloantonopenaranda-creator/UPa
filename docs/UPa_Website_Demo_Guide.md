# UPa website — assignment demo

Demonstrate **Option 2: broker-based publish–subscribe**, from pages 4–6 of the project document, through UPa. Allow about **8 minutes**.

Open **http://127.0.0.1:8003/** on this computer, or use the current ngrok link on other devices. Use the **same website address** throughout.

See [all nine demo accounts and suggested uses](UPa_Demo_Accounts.md).

## Prepare before presenting

Use **three separate browser profiles or devices**. For example: Edge for Maria, Chrome for Luis, and Edge InPrivate for Bea. Ordinary tabs share browser storage and are unsuitable for this demonstration.

| Window | Account | Username | Password |
|---|---|---|---|
| 1 | Maria, property owner | `manager` | `manager123` |
| 2 | Luis, tenant | `luis` | `luis123` |
| 3 | Bea, tenant | `bea` | `bea123` |

1. Sign in to all three accounts to register their subscriptions.
2. Luis and Bea already have demo rooms at **Casa Jaro Dormitory**, so they can use maintenance immediately. If an existing browser has no room for them, Maria can assign available space through **Property tools → Tenant records**.
3. Confirm both tenants can open **Maintenance** and **Announcements**. Keep all three windows open. Use fresh demo titles if repeating the presentation.

If the website is closed, run this from PowerShell and leave it open:

```powershell
Set-Location 'C:\Users\Client\Desktop\UPa'
.\scripts\pyrun.bat -m pubsub.webapp --port 8003 --broker-port 15613 --data-dir data --no-browser
```

## 1. Introduce the system

Show the three signed-in accounts.

**Say:** “UPa sends events through a gateway to a Python publish–subscribe broker. The broker delivers them to matching subscribers. We have two publisher clients, one for owner actions and one for tenant actions. We will demonstrate delivery to three account subscribers.”

## 2. Send one announcement to two subscribers

**Maria:** Open **Property tools → Announcements**. Select **Casa Jaro Dormitory** and post:

- Title: **DEMO-1 Water tank cleaning**
- Message: **Water tank cleaning on Friday at 9 AM.**

**Luis and Bea:** Open **My home tools → Announcements**. Show that both received it; their notification bells also contain the notice.

**Say:** “One publication reaches both matching subscribers.”

## 3. Send requests while the owner is offline

**Maria:** Use **avatar → Log out**. Ensure Maria is signed out everywhere. **Wait 10 seconds.**

**Luis:** Open **My home tools → Maintenance → New Request**. Submit **DEMO-2 No Internet**, category **Internet**, priority **Medium**, description **No connection in my room.**

**Bea:** Submit **DEMO-3 Leaking faucet**, with a suitable category and description.

**Show:** Each tenant sees their own request as **Submitted**.

**Say:** “The tenant publisher sends requests while the owner subscriber is offline. The broker keeps the deliveries waiting.”

## 4. Reconnect the owner

**Maria:** Sign in again. Watch for the brief notice about alerts delivered from the broker queue. Open **Property tools → Maintenance** and show both new requests.

**Say:** “The owner catches up after reconnecting. The publishers and subscriber did not need to be online together.”

## 5. Show private topic filtering

**Maria:** Open **DEMO-2 No Internet**, change its status to **Acknowledged**, add **Request received**, and save.

**Luis:** Show the updated ticket and notification.

**Bea:** Check her maintenance list and notification bell. She has her faucet request, but no Luis ticket or private update.

**Say:** “Both tenants receive the shared property notice. Only Luis receives updates sent to his personal topic.”

## 6. Queue two updates for an offline tenant

**Luis:** Log out everywhere. **Wait 10 seconds.**

**Maria:** Update **DEMO-2 No Internet** twice:

1. **In progress** — note: **Checking the router.** Save.
2. **Resolved** — note: **Connection restored.** Save.

**Say:** “Luis's subscription remains registered while he is offline. The broker queues both deliveries.”

## 7. Reconnect Luis and verify catch-up

**Luis:** Sign in. Watch the queued-alert notice, then open **Maintenance → DEMO-2 No Internet**.

**Show:** The status is **Resolved**, and the history contains **Submitted → Acknowledged → In progress → Resolved**, with the notes. Refresh and confirm the ticket and history remain.

**Say:** “Luis receives the missed updates. The website saves received events before acknowledging them. Unacknowledged events can be delivered again: this is at-least-once delivery.”

Refreshing confirms the saved result. It does not force redelivery or simulate a broker crash; those controls are in the separate broker guide.

## 8. Connect the demonstration to the requirements

| Requirement | Website evidence |
|---|---|
| Python broker and real-world scenario | Property announcements and maintenance requests pass through the gateway and Python broker. |
| At least two publishers | `dormcare-web-manager` and `dormcare-web-tenant`. Luis and Bea share the tenant publisher connection. |
| At least three subscribers across topics | Maria: `manager.m1`; Luis: `tenant.u-luis`; Bea: `tenant.u-bea`. Both tenants also subscribe to `casa-jaro.announcements`. |
| Topic filtering | Both receive the announcement; only Luis receives his private ticket update. |
| Persistent offline queues and live catch-up | Maria receives requests after reconnecting; Luis receives both missed updates. |
| At-least-once delivery | Explain save-before-ACK and retries. The website shows offline delivery; forcing a duplicate requires the broker fault demonstration. |

**Finish:** “Topic-based filtering selects a named channel. Content-based filtering would inspect message fields, such as priority equals High. We chose topics because property membership and account identity provide clear delivery groups. The broker separates publishers from subscribers and supports delivery across disconnections.”

If time remains, briefly show **Community**, **My profile**, **Find rentals**, or **Roommates** as product features.

## If something does not arrive

- Check separate browser profiles/devices, the same website address, and approved Casa Jaro tenancies.
- For offline steps, log out, wait 10 seconds, and ensure that account is not open elsewhere. Switching tabs does not disconnect it.
- For an older missing submitted ticket, open it as the tenant and click **Resend to property owner**.
- If the queue notice disappears, show the received tickets and status history. The website has no queue counter.

This covers the live demonstration. The design report, textbook citations, fault test report, source/setup files, and individual reflections remain separate submission requirements.
