import { FormEvent, useEffect, useMemo, useState } from "react";

import {
  Row,
  formatWhen,
  list,
  object,
  splitList,
  text,
  useIntegrationRuntime,
} from "./runtime";

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty-panel">{children}</div>;
}

function eventDate(value: unknown) {
  const row = object(value);
  return text(row.dateTime || row.date);
}

function localInputValue(value: unknown) {
  const raw = text(value);
  if (!raw || !raw.includes("T")) return "";
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return "";
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function isoFromLocal(value: FormDataEntryValue | null) {
  const raw = text(value);
  if (!raw) return "";
  const date = new Date(raw);
  return Number.isNaN(date.getTime()) ? raw : date.toISOString();
}

export function CalendarPanel() {
  const runtime = useIntegrationRuntime();
  const [calendars, setCalendars] = useState<Row[]>([]);
  const [calendarId, setCalendarId] = useState("primary");
  const [events, setEvents] = useState<Row[]>([]);
  const [selected, setSelected] = useState<Row | null>(null);
  const [freeBusy, setFreeBusy] = useState<Row | null>(null);
  const listReady = runtime.available("google.calendar.list_events");
  const calendarListReady = runtime.available("google.calendar.list_calendars");
  const freeBusyReady = runtime.available("google.calendar.freebusy");

  async function loadCalendars() {
    if (!calendarListReady) return;
    await runtime.invoke(
      "google.calendar.list_calendars",
      {},
      "List Google calendars visible to this workspace connection",
      (value) => {
        const rows = list(object(value).calendars);
        setCalendars(rows);
        if (calendarId === "primary") {
          const primary = rows.find((item) => Boolean(item.primary));
          if (primary?.id) setCalendarId(text(primary.id));
        }
      },
    );
  }

  async function loadEvents(targetCalendarId = calendarId) {
    if (!listReady) return;
    const start = new Date();
    const end = new Date(start.getTime() + 30 * 24 * 60 * 60 * 1000);
    await runtime.invoke(
      "google.calendar.list_events",
      {
        time_min: start.toISOString(),
        time_max: end.toISOString(),
        calendar_id: targetCalendarId || "primary",
        limit: 50,
      },
      "Load the next 30 days of Google Calendar events",
      (value) => setEvents(list(object(value).events)),
    );
  }

  async function saveEvent(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    const eventId = text(selected?.id);
    const args: Row = {
      summary: text(fields.get("summary")),
      start: isoFromLocal(fields.get("start")),
      end: isoFromLocal(fields.get("end")),
      attendees: splitList(fields.get("attendees")),
      description: text(fields.get("description")),
      location: text(fields.get("location")),
      calendar_id: calendarId || "primary",
    };
    if (eventId) {
      args.event_id = eventId;
    } else {
      args.add_video_conference = fields.get("meet") === "on";
    }
    const capability = eventId
      ? "google.calendar.update_event"
      : "google.calendar.create_event";
    await runtime.invoke(
      capability,
      args,
      `${eventId ? "Update" : "Create"} calendar event “${text(args.summary)}”`,
      async () => {
        setSelected(null);
        await loadEvents();
      },
    );
  }

  async function deleteEvent() {
    const eventId = text(selected?.id);
    if (!eventId) return;
    await runtime.invoke(
      "google.calendar.delete_event",
      { event_id: eventId, calendar_id: calendarId || "primary" },
      `Delete calendar event “${text(selected?.summary, "Untitled event")}”`,
      async () => {
        setSelected(null);
        await loadEvents();
      },
    );
  }

  async function checkFreeBusy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    const ids = splitList(fields.get("calendar_ids"));
    await runtime.invoke(
      "google.calendar.freebusy",
      {
        time_min: isoFromLocal(fields.get("time_min")),
        time_max: isoFromLocal(fields.get("time_max")),
        calendar_ids: ids.length ? ids : [calendarId || "primary"],
      },
      `Check free/busy for ${ids.length || 1} calendar${ids.length === 1 ? "" : "s"}`,
      (value) => setFreeBusy(object(value)),
    );
  }

  useEffect(() => {
    if (!listReady) return;
    void loadEvents(calendarId);
    if (calendarListReady && calendars.length === 0) void loadCalendars();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listReady, calendarListReady, runtime.workspace.id]);

  const selectedIsAllDay = Boolean(text(object(selected?.start).date));
  const selectedLink = text(selected?.html_link);
  const selectedMeet = text(selected?.hangout_link);
  const selectedAttendees = useMemo(
    () =>
      list(selected?.attendees)
        .map((person) => text(person.email))
        .filter(Boolean)
        .join(", "),
    [selected],
  );

  const tomorrow = new Date(Date.now() + 24 * 60 * 60 * 1000);
  tomorrow.setMinutes(0, 0, 0);
  const tomorrowEnd = new Date(tomorrow.getTime() + 60 * 60 * 1000);
  const defaultStart = localInputValue(tomorrow.toISOString());
  const defaultEnd = localInputValue(tomorrowEnd.toISOString());

  return (
    <section className="integration-calendar-layout">
      <article className="data-card integration-list-pane">
        <div className="card-heading">
          <div>
            <span className="eyebrow">Google Workspace</span>
            <h2>Next 30 days</h2>
          </div>
          <button type="button" onClick={() => void loadEvents()} disabled={!listReady}>
            Refresh
          </button>
        </div>
        {calendars.length > 0 && (
          <label className="integration-inline-field">
            Calendar
            <select
              value={calendarId}
              onChange={(event) => {
                const next = event.target.value;
                setCalendarId(next);
                setSelected(null);
                void loadEvents(next);
              }}
            >
              {calendars.map((calendar) => (
                <option key={text(calendar.id)} value={text(calendar.id)}>
                  {text(calendar.summary, text(calendar.id))}
                  {calendar.primary ? " · Primary" : ""}
                </option>
              ))}
            </select>
          </label>
        )}
        <div className="integration-scroll-list">
          {events.map((item) => (
            <button
              type="button"
              key={text(item.id)}
              className={selected?.id === item.id ? "active" : ""}
              onClick={() => setSelected(item)}
            >
              <strong>{text(item.summary, "Untitled event")}</strong>
              <span>{formatWhen(eventDate(item.start))}</span>
              <p>
                {text(item.location) ||
                  list(item.attendees)
                    .map((person) => text(person.email))
                    .filter(Boolean)
                    .join(", ")}
              </p>
            </button>
          ))}
          {!events.length && <Empty>No upcoming events loaded.</Empty>}
        </div>
      </article>

      <div className="integration-detail-stack">
        <article className="data-card">
          <div className="card-heading">
            <div>
              <span className="eyebrow">{selected ? "Event" : "Create event"}</span>
              <h2>{selected ? text(selected.summary, "Event") : "New meeting"}</h2>
            </div>
            {selected && (
              <button type="button" onClick={() => setSelected(null)}>
                New event
              </button>
            )}
          </div>

          {selectedIsAllDay ? (
            <div className="integration-readonly-event">
              <p>
                This is an all-day event. The current deterministic Calendar writer handles
                timed events; viewing remains available here without silently changing it into
                a timed meeting.
              </p>
              <strong>{eventDate(selected?.start)}</strong>
              {selectedLink && (
                <a className="button-link" href={selectedLink} target="_blank" rel="noreferrer">
                  Open in Google Calendar ↗
                </a>
              )}
            </div>
          ) : (
            <form
              key={text(selected?.id, "new")}
              className="integration-form"
              onSubmit={saveEvent}
            >
              <label>
                Title
                <input name="summary" required defaultValue={text(selected?.summary)} />
              </label>
              <div className="integration-form-row">
                <label>
                  Start
                  <input
                    type="datetime-local"
                    name="start"
                    required
                    defaultValue={
                      selected ? localInputValue(eventDate(selected.start)) : defaultStart
                    }
                  />
                </label>
                <label>
                  End
                  <input
                    type="datetime-local"
                    name="end"
                    required
                    defaultValue={
                      selected ? localInputValue(eventDate(selected.end)) : defaultEnd
                    }
                  />
                </label>
              </div>
              <label>
                Attendees
                <input
                  name="attendees"
                  defaultValue={selectedAttendees}
                  placeholder="email@example.com, another@example.com"
                />
              </label>
              <label>
                Location
                <input name="location" defaultValue={text(selected?.location)} />
              </label>
              <label>
                Description
                <textarea
                  name="description"
                  rows={5}
                  defaultValue={text(selected?.description)}
                />
              </label>
              {!selected && (
                <label className="integration-check">
                  <input type="checkbox" name="meet" /> Add Google Meet
                </label>
              )}
              {selectedMeet && (
                <a className="button-link" href={selectedMeet} target="_blank" rel="noreferrer">
                  Open Google Meet ↗
                </a>
              )}
              <div className="row-actions">
                {selected && (
                  <button
                    type="button"
                    className="danger-button"
                    disabled={!runtime.available("google.calendar.delete_event")}
                    onClick={() => void deleteEvent()}
                  >
                    Delete
                  </button>
                )}
                <button
                  className="primary-button"
                  disabled={
                    !runtime.available(
                      selected
                        ? "google.calendar.update_event"
                        : "google.calendar.create_event",
                    )
                  }
                >
                  {selected ? "Review update" : "Review & create"}
                </button>
              </div>
              {selectedLink && (
                <a href={selectedLink} target="_blank" rel="noreferrer">
                  Open in Google Calendar ↗
                </a>
              )}
            </form>
          )}
        </article>

        <article className="data-card">
          <div className="card-heading">
            <div>
              <span className="eyebrow">Availability</span>
              <h2>Free / busy</h2>
            </div>
            <span className={`status-chip ${freeBusyReady ? "status-active" : ""}`}>
              {freeBusyReady ? "Available" : "Needs scope"}
            </span>
          </div>
          <form className="integration-form" onSubmit={checkFreeBusy}>
            <label>
              Calendar IDs or attendee emails
              <input
                name="calendar_ids"
                placeholder={calendarId || "person@example.com"}
              />
            </label>
            <div className="integration-form-row">
              <label>
                From
                <input type="datetime-local" name="time_min" required defaultValue={defaultStart} />
              </label>
              <label>
                To
                <input
                  type="datetime-local"
                  name="time_max"
                  required
                  defaultValue={localInputValue(
                    new Date(tomorrow.getTime() + 8 * 60 * 60 * 1000).toISOString(),
                  )}
                />
              </label>
            </div>
            <button disabled={!freeBusyReady}>Check availability</button>
          </form>
          {freeBusy && (
            <pre className="integration-structured-result">
              {JSON.stringify(freeBusy.calendars || freeBusy, null, 2)}
            </pre>
          )}
        </article>
      </div>
    </section>
  );
}
