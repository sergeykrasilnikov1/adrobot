const createForm = document.querySelector("#create");
const openForm = document.querySelector("#open");
const campaigns = document.querySelector("#campaigns");
const more = document.querySelector("#more");
const editor = document.querySelector("#editor");
const notice = document.querySelector("#notice");
let offerCatalog = [];
let campaign;
let stats;
let offset = 0;
let requestVersion = 0;

function element(tag, text, attrs = {}) {
  const node = document.createElement(tag);
  node.textContent = text;
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

function button(label, handler, style = "") {
  const node = element("button", label, {type: "button", class: style});
  node.addEventListener("click", () => run(node, handler));
  return node;
}

function message(text) {
  notice.hidden = !text;
  notice.textContent = text || "";
}

async function api(path, data) {
  const response = await fetch("/api/" + path, data === undefined ? {} : {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(data),
  });
  const body = await response.json();
  if (!response.ok) {
    const detail = Array.isArray(body.detail) ? body.detail.map(item => item.msg).join(" ") : body.detail;
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return body;
}

async function run(control, action) {
  control.disabled = true;
  message("");
  try {
    await action();
  } catch (error) {
    message(error.message);
  } finally {
    control.disabled = false;
  }
}

function fill(select, items, placeholder) {
  select.replaceChildren(new Option(placeholder, ""));
  for (const item of items) select.add(new Option(`${item.name} · #${item.id}`, item.id));
}

async function loadCatalog() {
  const {domains, groups, sources, offers} = await api("catalog");
  offerCatalog = offers;
  fill(createForm.elements.domain_id, domains, "Выберите домен");
  fill(createForm.elements.group_id, groups, "Выберите группу");
  fill(createForm.elements.traffic_source_id, sources, "Выберите источник");
  fill(createForm.elements.offer_id, offers.filter(available), "Выберите оффер по названию или ID");
  if (campaign) renderEditor();
}

function available(offer) {
  return offer.state !== "disabled" && offer.state !== "deleted";
}

async function loadCampaigns() {
  const rows = await api(`campaigns?offset=${offset}`);
  for (const row of rows) {
    if (row.state !== "deleted" && ![...campaigns.options].some(option => option.value === String(row.id))) {
      campaigns.add(new Option(`${row.name} · #${row.id}`, row.id));
    }
  }
  offset += rows.length;
  more.hidden = rows.length < 100;
}

async function openCampaign(id) {
  const version = ++requestVersion;
  const loaded = await api(`campaigns/${id}`);
  if (version !== requestVersion) return;
  campaign = loaded;
  stats = null;
  renderEditor();
  try {
    const report = await api(`campaigns/${id}/stats`);
    if (!Array.isArray(report.rows)) throw new Error("Неожиданный формат отчёта.");
    if (version === requestVersion) {
      stats = report.rows;
      renderEditor();
    }
  } catch (error) {
    if (version === requestVersion) message(`Потоки загружены. Статистика недоступна. ${error.message}`);
  }
}

function renderEditor() {
  const heading = element("div", "", {class: "heading"});
  const actions = element("div", "", {class: "actions"});
  actions.append(
    button("Обновить из Keitaro", () => openCampaign(campaign.id)),
    element("a", "Открыть в Keitaro ↗", {href: campaign.url, target: "_blank", rel: "noopener noreferrer"}),
  );
  heading.append(element("h2", campaign.name), actions);
  editor.replaceChildren(heading, element("p", "Статистика за сегодня по часовому поясу трекера", {class: "muted"}));
  for (const flow of campaign.flows) editor.append(renderFlow(flow));
  if (!campaign.flows.length) editor.append(element("p", "В кампании нет потоков."));
  editor.hidden = false;
}

function renderFlow(flow) {
  const section = element("article", "", {class: flow.dirty ? "flow dirty" : "flow"});
  const heading = element("div", "", {class: "heading"});
  const title = element("h3", `${flow.name} · #${flow.id}`);
  heading.append(title);
  if (flow.dirty) {
    const actions = element("div", "", {class: "actions"});
    actions.append(
      button("Опубликовать в Keitaro", () => editFlow(flow, "publish"), "primary"),
      button("Отменить", () => editFlow(flow, "cancel")),
    );
    heading.append(actions);
  }
  section.append(heading);
  if (stats) {
    const clicks = stats.filter(row => Number(row.stream_id) === flow.id)
      .reduce((sum, row) => sum + Number(row.clicks ?? 0), 0);
    section.append(element("p", `Клики за сегодня: ${clicks.toLocaleString("ru-RU")}`, {class: "muted"}));
  }
  if (flow.conflict) section.append(element("p", "Офферы изменились в Keitaro после открытия черновика.", {class: "warning"}));
  if (flow.schema !== "landings") {
    section.append(element("p", "Поток без схемы офферов", {class: "muted"}));
    return section;
  }
  const table = element("table", "");
  const header = element("tr", "");
  for (const label of ["Оффер", "Доля", "Клики", "Конверсии", "Выручка", "Прибыль", "Действия"]) {
    header.append(element("th", label, {scope: "col"}));
  }
  table.createTHead().append(header);
  if (!flow.offers.length) section.append(element("p", "В потоке нет офферов.", {class: "muted"}));
  const body = table.createTBody();
  for (const offer of flow.offers) body.append(renderOffer(flow, offer));
  const scroll = element("div", "", {class: "scroll"});
  scroll.append(table);
  section.append(scroll);
  const add = element("form", "", {class: "add"});
  const select = element("select", "", {"aria-label": "Добавить оффер по названию или ID", required: ""});
  const used = new Set(flow.offers.map(offer => offer.offer_id));
  const candidates = offerCatalog.filter(offer => available(offer) && !used.has(offer.id));
  const search = element("input", "", {type: "search", placeholder: "Название или ID",
    "aria-label": "Поиск оффера"});
  function filterOffers() {
    const term = search.value.trim().toLocaleLowerCase("ru");
    const matches = candidates.filter(offer => offer.name.toLocaleLowerCase("ru").includes(term)
      || String(offer.id).includes(term));
    fill(select, matches, matches.length ? "Выберите оффер" : "Нет доступных офферов");
    if (term && matches.length === 1) select.selectedIndex = 1;
  }
  search.addEventListener("input", filterOffers);
  filterOffers();
  const submit = element("button", "Добавить", {type: "submit"});
  add.append(search, select, submit);
  add.addEventListener("submit", event => {
    event.preventDefault();
    run(submit, () => editFlow(flow, "add", Number(select.value)));
  });
  section.append(add);
  return section;
}

function renderOffer(flow, offer) {
  const row = element("tr", "", {class: offer.state === "active" ? "" : "removed"});
  const label = offerCatalog.find(item => item.id === offer.offer_id)?.name;
  row.append(element("td", label ? `${label} · #${offer.offer_id}` : `#${offer.offer_id}`));
  const share = row.insertCell();
  const actions = element("td", "");
  if (offer.state === "active") {
    const input = element("input", "", {type: "number", value: offer.share, min: 1, max: 100,
      required: "", "aria-label": `Доля оффера ${offer.offer_id} в процентах`});
    share.append(input, document.createTextNode(" %"));
    actions.append(
      offer.pinned
        ? button("Открепить", () => editFlow(flow, "unpin", offer.offer_id))
        : button("Закрепить", () => input.reportValidity() && editFlow(flow, "pin", offer.offer_id, input.valueAsNumber)),
      document.createTextNode(" "),
      button("Убрать", () => editFlow(flow, "remove", offer.offer_id)),
    );
    input.readOnly = offer.pinned;
  } else {
    share.textContent = "0 %";
    actions.append(button("Вернуть", () => editFlow(flow, "restore", offer.offer_id)));
  }
  const entry = stats?.find(item => Number(item.stream_id) === flow.id && Number(item.offer_id) === offer.offer_id);
  for (const metric of ["clicks", "conversions", "revenue", "profit"]) {
    const value = entry?.[metric] ?? (stats && !entry ? 0 : null);
    row.append(element("td", value == null ? "—" : Number(value).toLocaleString("ru-RU", {
      maximumFractionDigits: 4,
    })));
  }
  row.append(actions);
  return row;
}

async function editFlow(flow, action, offer_id, share) {
  const id = campaign.id;
  const updated = await api(`campaigns/${id}/flows/${flow.id}/edit`, {
    action, offer_id, share, revision: flow.revision,
  });
  if (campaign.id === id) {
    campaign.flows = campaign.flows.map(item => item.id === updated.id ? updated : item);
    renderEditor();
  }
}

createForm.addEventListener("submit", event => {
  event.preventDefault();
  run(event.submitter, async () => {
    const data = Object.fromEntries(new FormData(createForm));
    const created = await api("campaigns", data);
    createForm.reset();
    campaigns.add(new Option(`${created.name} · #${created.id}`, created.id));
    campaigns.value = created.id;
    openForm.elements.campaign.value = created.id;
    await openCampaign(created.id);
  });
});

openForm.addEventListener("submit", event => {
  event.preventDefault();
  run(event.submitter, () => openCampaign(openForm.elements.campaign.value));
});
campaigns.addEventListener("change", () => { openForm.elements.campaign.value = campaigns.value; });
more.addEventListener("click", () => run(more, loadCampaigns));
campaigns.add(new Option("Выберите кампанию", ""));
Promise.allSettled([loadCatalog(), loadCampaigns()]).then(results => {
  message(results.filter(result => result.status === "rejected").map(result => result.reason.message).join(" "));
});
