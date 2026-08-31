import { FormEvent, useEffect, useMemo, useState } from "react";

import { Row, list, object, text, useIntegrationRuntime } from "./runtime";

const SUPPORTED_EXPORTS = new Set(["pdf", "jpg", "png", "gif", "pptx", "mp4"]);
type JobKind = "export" | "autofill";

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty-panel">{children}</div>;
}

function extractItems(value: unknown) {
  const body = object(value);
  return list(body.items || body.designs || body.brand_templates);
}

function datasetFrom(value: unknown) {
  const body = object(value);
  return object(body.dataset || body);
}

function jobFrom(value: unknown) {
  const body = object(value);
  return object(body.job || body);
}

function resultLinks(job: Row) {
  const directUrls = Array.isArray(job.urls)
    ? job.urls.map((item) => text(item)).filter(Boolean)
    : [];
  const result = object(job.result);
  const design = object(result.design);
  const designUrls = object(design.urls);
  const links = [
    ...directUrls,
    text(designUrls.edit_url),
    text(designUrls.view_url),
    text(design.url),
  ].filter(Boolean);
  return [...new Set(links)];
}

function parseTableValue(raw: string, fieldName: string, fieldType: "chart" | "sheet") {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new Error(`${fieldName} must contain valid JSON table data`);
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`${fieldName} table data must be a JSON object`);
  }
  return {
    type: fieldType,
    [fieldType === "chart" ? "chart_data" : "sheet_data"]: parsed,
  };
}

function buildAutofillData(dataset: Row, fields: FormData) {
  const data: Row = {};
  for (const [fieldName, definition] of Object.entries(dataset)) {
    const kind = text(object(definition).type, "text");
    const raw = text(fields.get(`field:${fieldName}`)).trim();
    if (!raw) continue;
    if (kind === "image" || kind === "video") {
      data[fieldName] = { type: kind, asset_id: raw };
    } else if (kind === "chart" || kind === "sheet") {
      data[fieldName] = parseTableValue(raw, fieldName, kind);
    } else {
      data[fieldName] = { type: "text", text: raw };
    }
  }
  if (!Object.keys(data).length) {
    throw new Error("Enter at least one Canva Autofill field.");
  }
  return data;
}

function AutofillFields({ dataset, uploads }: { dataset: Row; uploads: Row[] }) {
  if (!Object.keys(dataset).length) {
    return <Empty>No Data Autofill fields are exposed.</Empty>;
  }
  return (
    <>
      <datalist id="canva-upload-assets">
        {uploads.map((asset) => (
          <option
            key={text(asset.id)}
            value={text(asset.id)}
            label={text(asset.name || asset.title, "Canva upload")}
          />
        ))}
      </datalist>
      {Object.entries(dataset).map(([name, definition]) => {
        const kind = text(object(definition).type, "text");
        const complex = kind === "chart" || kind === "sheet";
        return (
          <label key={name}>
            {name}
            <small>
              {kind === "image" || kind === "video"
                ? `${kind} · Canva asset ID`
                : complex
                  ? `${kind} · table JSON`
                  : kind}
            </small>
            {complex ? (
              <textarea
                name={`field:${name}`}
                rows={6}
                placeholder='{"rows":[{"cells":[{"type":"string","value":"Example"}]}]}'
              />
            ) : (
              <input
                name={`field:${name}`}
                list={kind === "image" || kind === "video" ? "canva-upload-assets" : undefined}
                placeholder={
                  kind === "image" || kind === "video"
                    ? "Select or paste a Canva asset ID"
                    : `Value for ${name}`
                }
              />
            )}
          </label>
        );
      })}
    </>
  );
}

export function CanvaPanel() {
  const runtime = useIntegrationRuntime();
  const [designs, setDesigns] = useState<Row[]>([]);
  const [selected, setSelected] = useState<Row | null>(null);
  const [dataset, setDataset] = useState<Row>({});
  const [templates, setTemplates] = useState<Row[]>([]);
  const [template, setTemplate] = useState<Row | null>(null);
  const [templateDataset, setTemplateDataset] = useState<Row>({});
  const [uploads, setUploads] = useState<Row[]>([]);
  const [exportFormats, setExportFormats] = useState<string[]>([]);
  const [jobKind, setJobKind] = useState<JobKind | null>(null);
  const [job, setJob] = useState<Row | null>(null);
  const [inputError, setInputError] = useState<string | null>(null);
  const designsReady = runtime.available("canva.designs.list");

  async function loadDesigns() {
    if (!designsReady) return;
    await runtime.invoke(
      "canva.designs.list",
      { ownership: "any", sort_by: "modified_descending" },
      "Load recent Canva designs",
      (value) => setDesigns(extractItems(value)),
    );
  }

  async function loadTemplates() {
    if (!runtime.available("canva.brand_templates.list")) return;
    await runtime.invoke(
      "canva.brand_templates.list",
      {
        dataset: "non_empty",
        ownership: "any",
        sort_by: "modified_descending",
        limit: 50,
      },
      "Load autofill-enabled Canva brand templates",
      (value) => setTemplates(extractItems(value)),
    );
  }

  async function loadUploads() {
    if (!runtime.available("canva.folder.items.list")) return;
    await runtime.invoke(
      "canva.folder.items.list",
      { folder_id: "uploads", limit: 100, item_types: ["image"] },
      "Load Canva Uploads for image autofill",
      (value) => setUploads(extractItems(value)),
    );
  }

  async function selectDesign(design: Row) {
    const designId = text(design.id);
    if (!designId) return;
    setInputError(null);
    setDataset({});
    setExportFormats([]);
    await runtime.invoke(
      "canva.design.get",
      { design_id: designId },
      `Open Canva design “${text(design.title, designId)}”`,
      (value) => {
        const body = object(value);
        setSelected(Object.keys(object(body.design)).length ? object(body.design) : body);
      },
    );
    if (runtime.available("canva.design.dataset")) {
      await runtime.invoke(
        "canva.design.dataset",
        { design_id: designId },
        "Read Canva design autofill fields",
        (value) => setDataset(datasetFrom(value)),
      );
    }
    if (runtime.available("canva.design.export_formats")) {
      await runtime.invoke(
        "canva.design.export_formats",
        { design_id: designId },
        "Read supported Canva export formats",
        (value) => {
          const formats = object(object(value).formats);
          setExportFormats(
            Object.keys(formats).filter((format) => SUPPORTED_EXPORTS.has(format)),
          );
        },
      );
    }
  }

  async function selectTemplate(row: Row) {
    const templateId = text(row.id);
    if (!templateId) return;
    setInputError(null);
    setTemplateDataset({});
    if (runtime.available("canva.brand_template.get")) {
      await runtime.invoke(
        "canva.brand_template.get",
        { brand_template_id: templateId },
        `Read Canva brand template “${text(row.title, templateId)}”`,
        (value) => {
          const body = object(value);
          setTemplate(
            Object.keys(object(body.brand_template)).length
              ? object(body.brand_template)
              : Object.keys(object(body.template)).length
                ? object(body.template)
                : body,
          );
        },
      );
    } else {
      setTemplate(row);
    }
    if (runtime.available("canva.brand_template.dataset")) {
      await runtime.invoke(
        "canva.brand_template.dataset",
        { brand_template_id: templateId },
        "Read Canva brand-template autofill fields",
        (value) => setTemplateDataset(datasetFrom(value)),
      );
    }
  }

  async function createDesign(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = new FormData(form);
    setInputError(null);
    await runtime.invoke(
      "canva.design.create",
      {
        title: text(fields.get("title")),
        preset: text(fields.get("preset"), "presentation"),
      },
      `Create Canva design “${text(fields.get("title"))}”`,
      async (value) => {
        const body = object(value);
        const design = Object.keys(object(body.design)).length ? object(body.design) : body;
        setSelected(design);
        form.reset();
        await loadDesigns();
      },
    );
  }

  async function autofillSelected(
    form: HTMLFormElement,
    mode: "update_design" | "create_from_design",
  ) {
    if (!selected) return;
    try {
      setInputError(null);
      const fields = new FormData(form);
      const data = buildAutofillData(dataset, fields);
      const title = text(fields.get("title"));
      await runtime.invoke(
        "canva.autofill.create",
        {
          type: mode,
          design_id: text(selected.id),
          ...(mode === "create_from_design" && title ? { title } : {}),
          data,
        },
        mode === "update_design"
          ? `Update Data Autofill fields in “${text(selected.title, text(selected.id))}”`
          : `Create an autofilled copy of “${text(selected.title, text(selected.id))}”`,
        (value) => {
          setJobKind("autofill");
          setJob(jobFrom(value));
          if (mode === "create_from_design") form.reset();
        },
      );
    } catch (caught) {
      setInputError(caught instanceof Error ? caught.message : "Invalid Canva Autofill data");
    }
  }

  async function autofillTemplate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!template) return;
    const form = event.currentTarget;
    try {
      setInputError(null);
      const fields = new FormData(form);
      const data = buildAutofillData(templateDataset, fields);
      await runtime.invoke(
        "canva.autofill.create",
        {
          type: "create_from_brand_template",
          brand_template_id: text(template.id),
          title: text(fields.get("title")),
          data,
        },
        `Create Canva design from “${text(template.title, "brand template")}”`,
        (value) => {
          setJobKind("autofill");
          setJob(jobFrom(value));
          form.reset();
        },
      );
    } catch (caught) {
      setInputError(caught instanceof Error ? caught.message : "Invalid Canva Autofill data");
    }
  }

  async function exportDesign(format: string) {
    const designId = text(selected?.id);
    if (!designId || !format) return;
    setInputError(null);
    await runtime.invoke(
      "canva.design.export.create",
      { design_id: designId, format },
      `Export Canva design as ${format.toUpperCase()}`,
      (value) => {
        setJobKind("export");
        setJob(jobFrom(value));
      },
    );
  }

  async function refreshJob() {
    const jobId = text(job?.id);
    if (!jobId || !jobKind) return;
    const capability =
      jobKind === "export" ? "canva.design.export.get" : "canva.autofill.get";
    const key = jobKind === "export" ? "export_id" : "job_id";
    await runtime.invoke(
      capability,
      { [key]: jobId },
      `Refresh Canva ${jobKind} job`,
      (value) => setJob(jobFrom(value)),
    );
  }

  useEffect(() => {
    if (!designsReady) return;
    if (designs.length === 0) void loadDesigns();
    if (runtime.available("canva.brand_templates.list") && templates.length === 0) {
      void loadTemplates();
    }
    if (runtime.available("canva.folder.items.list") && uploads.length === 0) {
      void loadUploads();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [designsReady, runtime.workspace.id, runtime.tools.length]);

  const editUrl = text(object(selected?.urls).edit_url || selected?.edit_url);
  const templateEditUrl = text(object(template?.urls).edit_url || template?.edit_url);
  const jobLinks = useMemo(() => (job ? resultLinks(job) : []), [job]);
  const formatOptions = exportFormats.length
    ? exportFormats
    : ["pdf", "jpg", "png", "gif", "pptx", "mp4"];

  return (
    <section className="integration-canva-layout">
      {inputError && <div className="inline-error page-error full-span">{inputError}</div>}

      <article className="data-card">
        <div className="card-heading">
          <div>
            <span className="eyebrow">Canva library</span>
            <h2>Designs</h2>
          </div>
          <button type="button" onClick={() => void loadDesigns()} disabled={!designsReady}>
            Refresh
          </button>
        </div>
        <form className="integration-form compact" onSubmit={createDesign}>
          <div className="integration-form-row">
            <label>
              New design title
              <input name="title" required placeholder="Campaign concept" />
            </label>
            <label>
              Type
              <select name="preset" defaultValue="presentation">
                <option value="presentation">Presentation</option>
                <option value="doc">Doc</option>
                <option value="email">Email</option>
                <option value="whiteboard">Whiteboard</option>
              </select>
            </label>
          </div>
          <button className="primary-button" disabled={!runtime.available("canva.design.create")}>
            Review & create design
          </button>
        </form>
        <div className="canva-card-grid">
          {designs.map((design) => {
            const thumbnailUrl = text(object(design.thumbnail).url);
            return (
              <button
                type="button"
                key={text(design.id)}
                className={selected?.id === design.id ? "active" : ""}
                onClick={() => void selectDesign(design)}
              >
                {thumbnailUrl && <img src={thumbnailUrl} alt="" />}
                <strong>{text(design.title, "Untitled design")}</strong>
                <small>{text(design.id)}</small>
              </button>
            );
          })}
          {!designs.length && <Empty>No Canva designs loaded.</Empty>}
        </div>
      </article>

      <article className="data-card">
        <div className="card-heading">
          <div>
            <span className="eyebrow">Data-aware authoring</span>
            <h2>{selected ? text(selected.title, "Selected design") : "Design editor"}</h2>
          </div>
        </div>
        {selected ? (
          <>
            <div className="row-actions integration-toolbar">
              {editUrl && (
                <a className="button-link" href={editUrl} target="_blank" rel="noreferrer">
                  Open full Canva editor ↗
                </a>
              )}
              <select
                aria-label="Export format"
                defaultValue=""
                onChange={(event) => {
                  const format = event.target.value;
                  event.target.value = "";
                  if (format) void exportDesign(format);
                }}
                disabled={!runtime.available("canva.design.export.create")}
              >
                <option value="">Export…</option>
                {formatOptions.map((format) => (
                  <option key={format} value={format}>
                    {format.toUpperCase()}
                  </option>
                ))}
              </select>
            </div>
            {Object.keys(dataset).length ? (
              <form className="integration-form">
                <p>
                  These are the fields Canva explicitly exposes through Data Autofill. Arbitrary
                  element-level editing stays in Canva's full editor.
                </p>
                <label>
                  New-copy title
                  <input name="title" placeholder={`${text(selected.title, "Design")} copy`} />
                </label>
                <AutofillFields dataset={dataset} uploads={uploads} />
                <div className="row-actions">
                  <button
                    type="button"
                    disabled={!runtime.available("canva.autofill.create")}
                    onClick={(event) => {
                      if (event.currentTarget.form) {
                        void autofillSelected(event.currentTarget.form, "create_from_design");
                      }
                    }}
                  >
                    Review & create copy
                  </button>
                  <button
                    type="button"
                    className="primary-button"
                    disabled={!runtime.available("canva.autofill.create")}
                    onClick={(event) => {
                      if (event.currentTarget.form) {
                        void autofillSelected(event.currentTarget.form, "update_design");
                      }
                    }}
                  >
                    Review & update in place
                  </button>
                </div>
              </form>
            ) : (
              <Empty>
                This design has no exposed Data Autofill fields. Use “Open full Canva editor” for
                arbitrary visual editing.
              </Empty>
            )}
          </>
        ) : (
          <Empty>Select a design to inspect it.</Empty>
        )}
      </article>

      <article className="data-card full-span">
        <div className="card-heading">
          <div>
            <span className="eyebrow">Brand templates</span>
            <h2>Autofill production</h2>
          </div>
          <button
            type="button"
            onClick={() => void loadTemplates()}
            disabled={!runtime.available("canva.brand_templates.list")}
          >
            Refresh templates
          </button>
        </div>
        <p className="integration-meta">
          Canva Autofill is plan-dependent. Operly exposes it only when the connected account has
          the required provider scopes and Canva permits the operation.
        </p>
        <div className="integration-template-layout">
          <div className="integration-scroll-list">
            {templates.map((row) => (
              <button
                type="button"
                key={text(row.id)}
                className={template?.id === row.id ? "active" : ""}
                onClick={() => void selectTemplate(row)}
              >
                <strong>{text(row.title, "Brand template")}</strong>
                <small>{text(row.id)}</small>
              </button>
            ))}
            {!templates.length && (
              <Empty>Reconnect Canva with brand-template scopes to use template autofill.</Empty>
            )}
          </div>
          <div>
            {template ? (
              <form className="integration-form" onSubmit={autofillTemplate}>
                <div className="card-heading">
                  <h3>{text(template.title, "Brand template")}</h3>
                  {templateEditUrl && (
                    <a href={templateEditUrl} target="_blank" rel="noreferrer">
                      Open in Canva ↗
                    </a>
                  )}
                </div>
                <label>
                  New design title
                  <input name="title" placeholder={text(template.title)} />
                </label>
                <AutofillFields dataset={templateDataset} uploads={uploads} />
                <button
                  className="primary-button"
                  disabled={!runtime.available("canva.autofill.create")}
                >
                  Review & create from template
                </button>
              </form>
            ) : (
              <Empty>Select an autofill-enabled template.</Empty>
            )}
          </div>
        </div>
      </article>

      <article className="data-card full-span">
        <div className="card-heading">
          <div>
            <span className="eyebrow">Canva jobs & assets</span>
            <h2>Output status</h2>
          </div>
          {job && (
            <button type="button" onClick={() => void refreshJob()}>
              Refresh {jobKind} job
            </button>
          )}
        </div>
        <div className="integration-asset-summary">
          <span>
            <strong>{uploads.length}</strong>
            <small>image assets visible in Uploads</small>
          </span>
          <button
            type="button"
            onClick={() => void loadUploads()}
            disabled={!runtime.available("canva.folder.items.list")}
          >
            Refresh Uploads
          </button>
        </div>
        {uploads.length > 0 && (
          <details>
            <summary>Available image asset IDs</summary>
            <div className="integration-token-list">
              {uploads.slice(0, 100).map((asset) => (
                <code key={text(asset.id)} title={text(asset.name || asset.title)}>
                  {text(asset.id)}
                </code>
              ))}
            </div>
          </details>
        )}
        {job ? (
          <div className="integration-job">
            <p>
              <strong>{jobKind === "export" ? "Export" : "Autofill"}</strong> · {text(job.status, "unknown")}
              {job.id ? ` · ${text(job.id)}` : ""}
            </p>
            {jobLinks.length > 0 && (
              <div className="row-actions">
                {jobLinks.map((url, index) => (
                  <a
                    className="button-link"
                    key={`${url}:${index}`}
                    href={url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {jobKind === "export" ? `Download ${index + 1} ↗` : "Open Canva result ↗"}
                  </a>
                ))}
              </div>
            )}
            <details>
              <summary>Raw job details</summary>
              <pre>{JSON.stringify(job, null, 2)}</pre>
            </details>
          </div>
        ) : (
          <Empty>Create an export or Autofill job to see its status here.</Empty>
        )}
      </article>
    </section>
  );
}
