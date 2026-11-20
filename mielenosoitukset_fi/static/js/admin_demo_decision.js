/*
 * Shared approve/reject confirmation for admin demo decisions.
 *
 *   const ok = await window.MosoDemoDecision.confirm(demoId, "approved" | "rejected");
 *
 * The dialog fetches a read-only preview of the decision's side effects
 * (submitter email, closed cases, revoked links, city queue) and resolves
 * true only when the admin presses the confirm button.
 */
(function () {
  "use strict";

  function modalEl() {
    return document.querySelector("[data-demo-decision-modal]");
  }

  function fill(template, count) {
    return String(template || "").replace("__COUNT__", String(count));
  }

  function effectLines(modal, data, decision) {
    const d = modal.dataset;
    const lines = [];
    if (data.cancelled) lines.push(d.cancelled);
    lines.push(decision === "approved" ? d.publicApproved : d.publicRejected);
    if (!data.status_changes) lines.push(d.emailUnchanged);
    else if (data.emails_submitter) lines.push(d.emailYes);
    else lines.push(d.emailNoAddress);
    if (data.open_cases > 0) lines.push(fill(d.cases, data.open_cases));
    if (data.links_revoked > 0) lines.push(fill(d.links, data.links_revoked));
    if (data.edit_links_revoked > 0) lines.push(d.editLinks);
    if (data.leaves_city_queue) lines.push(d.cityQueue);
    return lines;
  }

  function renderLines(list, lines) {
    list.replaceChildren(
      ...lines.map((text) => {
        const li = document.createElement("li");
        li.textContent = text;
        return li;
      })
    );
  }

  function confirmDecision(demoId, decision) {
    const modal = modalEl();
    if (!modal || !window.bootstrap) {
      // No dialog on this page: fail closed rather than act silently.
      return Promise.resolve(false);
    }
    const d = modal.dataset;
    const title = modal.querySelector("[data-demo-decision-title]");
    const demoLine = modal.querySelector("[data-demo-decision-demo]");
    const list = modal.querySelector("[data-demo-decision-effects]");
    const confirmBtn = modal.querySelector("[data-demo-decision-confirm]");
    const approving = decision === "approved";

    title.textContent = approving ? d.titleApproved : d.titleRejected;
    demoLine.textContent = "";
    confirmBtn.textContent = approving ? d.confirmApproved : d.confirmRejected;
    confirmBtn.className = "btn " + (approving ? "btn-success" : "btn-danger");
    confirmBtn.disabled = true;
    renderLines(list, [d.loading]);

    const url =
      d.previewUrlTemplate.replace("__DEMO_ID__", encodeURIComponent(demoId)) +
      "?decision=" + encodeURIComponent(decision);
    fetch(url, { headers: { Accept: "application/json" }, credentials: "same-origin" })
      .then((response) => (response.ok ? response.json() : Promise.reject(response)))
      .then((data) => {
        demoLine.textContent = data.title || "";
        renderLines(list, effectLines(modal, data, decision));
      })
      .catch(() => renderLines(list, [d.loadError]))
      .finally(() => {
        confirmBtn.disabled = false;
      });

    const instance = window.bootstrap.Modal.getOrCreateInstance(modal);
    return new Promise((resolve) => {
      let confirmed = false;
      const onConfirm = () => {
        confirmed = true;
        instance.hide();
      };
      const onHidden = () => {
        confirmBtn.removeEventListener("click", onConfirm);
        modal.removeEventListener("hidden.bs.modal", onHidden);
        resolve(confirmed);
      };
      confirmBtn.addEventListener("click", onConfirm);
      modal.addEventListener("hidden.bs.modal", onHidden);
      instance.show();
    });
  }

  window.MosoDemoDecision = { confirm: confirmDecision };
})();
