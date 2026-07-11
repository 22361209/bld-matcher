document.addEventListener("submit", (event) => {
  const submitter = event.submitter;
  if (!(submitter instanceof HTMLElement)) return;
  const message = submitter.dataset.confirm;
  if (message && !window.confirm(message)) {
    event.preventDefault();
  }
});

const hasFileDrag = (event) => {
  const types = event.dataTransfer ? Array.from(event.dataTransfer.types || []) : [];
  return types.includes("Files");
};

document.addEventListener("dragover", (event) => {
  if (!hasFileDrag(event)) return;
  event.preventDefault();
});

document.addEventListener("drop", (event) => {
  if (!hasFileDrag(event)) return;
  event.preventDefault();
});

document.querySelectorAll(".file-picker-input").forEach((input) => {
  const picker = input.closest(".file-picker");
  const name = picker ? picker.querySelector(".file-picker-name") : null;
  const oeInput = picker ? picker.querySelector(".file-picker-oe-input") : null;
  const clearButton = picker ? picker.querySelector(".file-picker-clear") : null;
  const dropStatus = picker ? picker.querySelector("[data-file-drop-status]") : null;
  const defaultDropStatus = dropStatus ? dropStatus.textContent.trim() : "";
  if (!name && !oeInput && !dropStatus) return;

  const setDropStatus = (message, isError = false) => {
    if (!(dropStatus instanceof HTMLElement)) return;
    dropStatus.textContent = message || defaultDropStatus;
    dropStatus.classList.toggle("error", isError);
  };

  const syncFilePicker = () => {
    const fileName = input.files && input.files.length ? input.files[0].name : "";
    if (name) {
      name.textContent = fileName || "未选择任何文件";
    }
    if (clearButton instanceof HTMLButtonElement) {
      clearButton.disabled = !fileName;
    }
    if (oeInput && fileName) {
      oeInput.value = fileName;
      oeInput.readOnly = true;
    } else if (oeInput) {
      oeInput.readOnly = false;
    }
    setDropStatus(fileName ? `已选择：${fileName}` : defaultDropStatus);
  };

  input.addEventListener("change", syncFilePicker);
  clearButton?.addEventListener("click", () => {
    input.value = "";
    if (oeInput instanceof HTMLInputElement) {
      oeInput.value = "";
      oeInput.readOnly = false;
    }
    syncFilePicker();
  });

  if (!picker || !picker.hasAttribute("data-file-drop-zone")) return;

  const acceptedExts = (picker.dataset.fileDropAccept || input.getAttribute("accept") || "")
    .split(",")
    .map((item) => item.trim().toLowerCase())
    .filter((item) => item.startsWith("."));
  const acceptedExtLabel = acceptedExts.length ? acceptedExts.join(" / ") : "文件";
  const acceptsFile = (file) => {
    if (!acceptedExts.length) return true;
    return acceptedExts.some((ext) => file.name.toLowerCase().endsWith(ext));
  };
  let dragDepth = 0;

  picker.addEventListener("dragenter", (event) => {
    if (!hasFileDrag(event)) return;
    event.preventDefault();
    dragDepth += 1;
    picker.classList.add("drag-over");
    setDropStatus(`松开即可导入 ${acceptedExtLabel} 文件`);
  });

  picker.addEventListener("dragover", (event) => {
    if (!hasFileDrag(event)) return;
    event.preventDefault();
    if (event.dataTransfer) {
      event.dataTransfer.dropEffect = "copy";
    }
  });

  picker.addEventListener("dragleave", (event) => {
    if (!hasFileDrag(event)) return;
    dragDepth -= 1;
    if (dragDepth > 0) return;
    picker.classList.remove("drag-over");
    syncFilePicker();
  });

  picker.addEventListener("drop", (event) => {
    if (!hasFileDrag(event)) return;
    event.preventDefault();
    dragDepth = 0;
    picker.classList.remove("drag-over");

    const files = event.dataTransfer ? Array.from(event.dataTransfer.files || []) : [];
    const file = files.find(acceptsFile);
    if (!file) {
      setDropStatus(`仅支持 ${acceptedExtLabel} 文件`, true);
      return;
    }
    if (typeof DataTransfer === "undefined") {
      setDropStatus("当前浏览器不支持拖入文件，请点击选择文件", true);
      return;
    }

    const transfer = new DataTransfer();
    transfer.items.add(file);
    input.files = transfer.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
    if (files.length > 1) {
      setDropStatus(`已选择：${file.name}（多个文件时仅使用第一个可用文件）`);
    }
  });
});

document.querySelectorAll("[data-quick-results]").forEach((panel) => {
  const cards = Array.from(panel.querySelectorAll("[data-quick-card]"));
  const filters = Array.from(panel.querySelectorAll("[data-quick-filter]"));
  const count = panel.querySelector("[data-quick-result-count]");
  const prefix = panel.querySelector("[data-quick-filter-prefix]");
  const empty = panel.querySelector("[data-quick-filter-empty]");
  let activeFilter = panel.dataset.initialFilter || "";

  const updateUrl = (filter) => {
    if (!window.history?.replaceState) return;
    const url = new URL(window.location.href);
    if (filter) {
      url.searchParams.set("quick_filter", filter);
    } else {
      url.searchParams.delete("quick_filter");
    }
    window.history.replaceState({}, "", url);
  };

  const applyFilter = (filter, updateHistory = false) => {
    activeFilter = filter || "";
    let visibleCount = 0;
    cards.forEach((card) => {
      const visible = !activeFilter || card.dataset.matchType === activeFilter;
      card.hidden = !visible;
      if (visible) visibleCount += 1;
    });
    filters.forEach((filterButton) => {
      const active = filterButton.dataset.quickFilter === activeFilter;
      filterButton.classList.toggle("active", active);
      filterButton.setAttribute("aria-pressed", active ? "true" : "false");
    });
    const activeButton = filters.find((filterButton) => filterButton.dataset.quickFilter === activeFilter);
    if (prefix instanceof HTMLElement) {
      prefix.textContent = activeButton ? `${activeButton.dataset.filterLabel || activeButton.textContent} · ` : "";
    }
    if (count instanceof HTMLElement) {
      count.textContent = `${visibleCount}`;
    }
    if (empty instanceof HTMLElement) {
      empty.hidden = visibleCount > 0;
    }
    if (updateHistory) {
      updateUrl(activeFilter);
    }
  };

  filters.forEach((filterButton) => {
    filterButton.addEventListener("click", (event) => {
      event.preventDefault();
      const nextFilter = activeFilter === filterButton.dataset.quickFilter ? "" : filterButton.dataset.quickFilter || "";
      applyFilter(nextFilter, true);
    });
  });

  applyFilter(activeFilter, false);
});

document.querySelectorAll("[data-history-loader]").forEach((drawer) => {
  const url = drawer.dataset.historyUrl || "";
  const count = drawer.querySelector("[data-history-count]");
  const tableCount = drawer.querySelector("[data-history-table-count]");
  const rows = drawer.querySelector("[data-history-rows]");
  const tableWrap = drawer.querySelector("[data-history-table-wrap]");
  const empty = drawer.querySelector("[data-history-empty]");
  const searchInput = drawer.querySelector("input[name='history_q']");
  let loaded = drawer.dataset.historyLoaded === "true";
  let loading = false;

  const setCount = (value) => {
    const text = `${value} 条`;
    if (count instanceof HTMLElement) count.textContent = text;
    if (tableCount instanceof HTMLElement) tableCount.textContent = text;
  };

  const appendCell = (row, text, href = "") => {
    const cell = document.createElement("td");
    if (href) {
      const link = document.createElement("a");
      link.href = href;
      link.textContent = text;
      cell.appendChild(link);
    } else {
      cell.textContent = text;
    }
    row.appendChild(cell);
  };

  const renderRows = (items) => {
    if (!(rows instanceof HTMLElement)) return;
    rows.replaceChildren();
    items.forEach((item) => {
      const row = document.createElement("tr");
      appendCell(row, item.name || "", item.download_url || "");
      appendCell(row, item.kind || "");
      appendCell(row, item.operator || "");
      appendCell(row, item.updated_at || "");
      appendCell(row, "下载", item.download_url || "");
      rows.appendChild(row);
    });
    if (tableWrap instanceof HTMLElement) tableWrap.hidden = items.length === 0;
    if (empty instanceof HTMLElement) {
      empty.hidden = items.length > 0;
      empty.textContent = items.length ? "" : "还没有历史报价文件。";
    }
    setCount(items.length);
  };

  const loadHistory = async () => {
    if (!url || loaded || loading) return;
    loading = true;
    if (count instanceof HTMLElement) count.textContent = "加载中";
    if (tableCount instanceof HTMLElement) tableCount.textContent = "加载中";
    if (empty instanceof HTMLElement) {
      empty.hidden = false;
      empty.textContent = "正在加载历史报价文件...";
    }
    try {
      const requestUrl = new URL(url, window.location.origin);
      const query = searchInput instanceof HTMLInputElement ? searchInput.value.trim() : "";
      if (query) requestUrl.searchParams.set("history_q", query);
      const response = await fetch(requestUrl, { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error("load failed");
      const payload = await response.json();
      renderRows(Array.isArray(payload.rows) ? payload.rows : []);
      loaded = true;
      drawer.dataset.historyLoaded = "true";
    } catch (_error) {
      if (count instanceof HTMLElement) count.textContent = "加载失败";
      if (tableCount instanceof HTMLElement) tableCount.textContent = "加载失败";
      if (empty instanceof HTMLElement) {
        empty.hidden = false;
        empty.textContent = "历史报价文件加载失败，请刷新后再试。";
      }
    } finally {
      loading = false;
    }
  };

  if (drawer.open) {
    loadHistory();
  }
  drawer.addEventListener("toggle", () => {
    if (drawer.open) loadHistory();
  });
});

document.querySelectorAll(".shipment-folder-picker").forEach((picker) => {
  const photoInput = picker.querySelector(".shipment-file-input");
  const status = picker.querySelector("[data-shipment-file-status]");
  const clearButton = picker.querySelector("[data-clear-shipment-files]");
  const dropStatus = picker.querySelector("[data-file-drop-status]");
  const form = picker.closest("[data-shipment-form]");
  const submitButton = form?.querySelector("[data-shipment-submit]");
  const runStatus = form?.querySelector("[data-shipment-run-status]");
  const progressPanel = form?.querySelector("[data-shipment-progress]");
  const progressText = form?.querySelector("[data-shipment-progress-text]");
  const progressPercent = form?.querySelector("[data-shipment-progress-percent]");
  const progressBar = form?.querySelector("[data-shipment-progress-bar]");
  const progressDetail = form?.querySelector("[data-shipment-progress-detail]");
  const liveResult = document.querySelector("[data-shipment-live-result]");
  const liveSummary = document.querySelector("[data-shipment-live-summary]");
  const liveMessage = document.querySelector("[data-shipment-live-message]");
  const liveDownload = document.querySelector("[data-shipment-live-download]");
  const liveStats = document.querySelector("[data-shipment-live-stats]");
  const liveSeconds = document.querySelector("[data-shipment-live-seconds]");
  const livePrompt = document.querySelector("[data-shipment-live-prompt]");
  const liveCompletion = document.querySelector("[data-shipment-live-completion]");
  const liveTotal = document.querySelector("[data-shipment-live-total]");
  const liveLinks = document.querySelector("[data-shipment-live-links]");
  const liveExcel = document.querySelector("[data-shipment-live-excel]");
  const liveJson = document.querySelector("[data-shipment-live-json]");
  const inputs = [photoInput].filter((input) => input instanceof HTMLInputElement);
  if (!inputs.length || !(status instanceof HTMLElement)) return;
  let pollTimer = null;

  const readJsonResponse = async (response, fallbackMessage) => {
    const contentType = response.headers.get("content-type") || "";
    const body = await response.text();
    if (contentType.includes("application/json")) {
      try {
        return JSON.parse(body);
      } catch (_error) {
        throw new Error(fallbackMessage);
      }
    }
    if (response.status === 401 || response.redirected || response.url.includes("/login")) {
      throw new Error("登录已失效，请刷新页面重新登录。");
    }
    if (response.status === 403) {
      throw new Error("当前账号没有权限执行这个操作。");
    }
    if (response.status === 413) {
      throw new Error("上传文件过大，请减少照片数量后再试。");
    }
    throw new Error(fallbackMessage);
  };

  const imageExts = [".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif"];
  const isImageFile = (file) => {
    const name = (file?.name || "").toLowerCase();
    return imageExts.some((ext) => name.endsWith(ext)) || String(file?.type || "").startsWith("image/");
  };
  const selectedFiles = () => Array.from(photoInput?.files || []);
  const setProgress = ({ percent = 0, text = "", detail = "" } = {}) => {
    const value = Math.max(0, Math.min(100, Number(percent) || 0));
    if (progressPanel instanceof HTMLElement) {
      progressPanel.hidden = false;
    }
    if (progressText instanceof HTMLElement) {
      progressText.textContent = text || "正在处理";
    }
    if (progressPercent instanceof HTMLElement) {
      progressPercent.textContent = `${Math.round(value)}%`;
    }
    if (progressBar instanceof HTMLElement) {
      progressBar.style.width = `${value}%`;
    }
    if (progressDetail instanceof HTMLElement) {
      progressDetail.textContent = detail;
    }
  };
  const showLiveResult = () => {
    if (liveResult instanceof HTMLDetailsElement) {
      liveResult.hidden = false;
      liveResult.open = true;
    }
  };
  const setSubmitEnabled = (enabled) => {
    if (submitButton instanceof HTMLButtonElement) {
      submitButton.disabled = !enabled;
      submitButton.textContent = enabled ? "开始识别" : "识别中...";
    }
  };
  const sync = () => {
    const files = selectedFiles().filter(isImageFile);
    if (files.length) {
      status.textContent = files.length === 1 ? `已选择 1 张照片：${files[0].name}` : `已选择 ${files.length} 张照片`;
    } else {
      status.textContent = "可多选照片，也可拖入整个文件夹";
    }
    if (clearButton instanceof HTMLButtonElement) {
      clearButton.disabled = !files.length;
    }
    if (dropStatus instanceof HTMLElement) {
      dropStatus.textContent = files.length ? "照片已选好，点击开始识别后上传" : "";
      dropStatus.classList.remove("error");
    }
    if (runStatus instanceof HTMLElement && !files.length) {
      runStatus.textContent = "";
      runStatus.classList.remove("active", "done", "error");
    }
    if (!files.length && progressPanel instanceof HTMLElement) {
      progressPanel.hidden = true;
      setProgress({ percent: 0, text: "等待开始", detail: "" });
    }
  };

  const pollJob = async (statusUrl) => {
    try {
      const response = await fetch(statusUrl, { headers: { Accept: "application/json" } });
      const data = await readJsonResponse(response, "读取进度失败");
      if (!response.ok || !data.ok) {
        throw new Error(data.error || "读取进度失败");
      }
      const job = data.job || {};
      const completed = Number(job.completed || 0);
      const total = Number(job.total || 0);
      const percent = Number(job.percent || 0);
      const summary = total ? `${completed}/${total} 张` : "准备中";
      const message = job.message || "正在处理";
      setProgress({
        percent,
        text: message,
        detail: job.current ? `当前照片：${job.current}` : summary,
      });
      showLiveResult();
      if (liveSummary instanceof HTMLElement) {
        liveSummary.textContent = `${summary} · ${Math.round(percent)}%`;
      }
      if (liveMessage instanceof HTMLElement) {
        liveMessage.textContent = message;
      }
      if (job.status === "completed") {
        window.clearInterval(pollTimer);
        pollTimer = null;
        setSubmitEnabled(true);
        if (runStatus instanceof HTMLElement) {
          runStatus.textContent = "识别完成，可以下载 Excel";
          runStatus.classList.add("active", "done");
          runStatus.classList.remove("error");
        }
        const result = job.result || {};
        status.textContent = `识别完成：${result.photos || completed} 张照片`;
        if (dropStatus instanceof HTMLElement) {
          dropStatus.textContent = "识别完成，可以下载结果";
          dropStatus.classList.remove("error");
        }
        setProgress({ percent: 100, text: "识别完成", detail: `照片 ${result.photos || completed} 张，标签 ${result.labels || 0} 张，失败 ${result.failed || 0} 张` });
        if (liveSummary instanceof HTMLElement) {
          liveSummary.textContent = `照片 ${result.photos || completed} 张 · 标签 ${result.labels || 0} 张 · 失败 ${result.failed || 0} 张 · Token ${result.total_tokens || 0}`;
        }
        if (liveStats instanceof HTMLElement) {
          liveStats.hidden = false;
        }
        if (liveSeconds instanceof HTMLElement) liveSeconds.textContent = `${result.seconds || result.elapsed_seconds || 0} 秒`;
        if (livePrompt instanceof HTMLElement) livePrompt.textContent = result.prompt_tokens || 0;
        if (liveCompletion instanceof HTMLElement) liveCompletion.textContent = result.completion_tokens || 0;
        if (liveTotal instanceof HTMLElement) liveTotal.textContent = result.total_tokens || 0;
        if (liveDownload instanceof HTMLAnchorElement && result.excel_url) {
          liveDownload.hidden = false;
          liveDownload.href = result.excel_url;
        }
        if (liveLinks instanceof HTMLElement) {
          liveLinks.hidden = false;
        }
        if (liveExcel instanceof HTMLAnchorElement && result.excel_url) {
          liveExcel.href = result.excel_url;
          liveExcel.textContent = result.excel_filename || "下载 Excel";
        }
        if (liveJson instanceof HTMLAnchorElement && result.json_url) {
          liveJson.href = result.json_url;
          liveJson.textContent = result.json_filename || "下载 JSON";
        }
        return;
      }
      if (job.status === "error") {
        throw new Error(job.error || job.message || "识别失败");
      }
    } catch (error) {
      window.clearInterval(pollTimer);
      pollTimer = null;
      setSubmitEnabled(true);
      const message = error instanceof Error ? error.message : "识别失败";
      if (dropStatus instanceof HTMLElement) {
        dropStatus.textContent = message;
        dropStatus.classList.add("error");
      }
      if (runStatus instanceof HTMLElement) {
        runStatus.textContent = message;
        runStatus.classList.add("active", "error");
        runStatus.classList.remove("done");
      }
      showLiveResult();
      if (liveSummary instanceof HTMLElement) {
        liveSummary.textContent = "识别失败";
      }
      if (liveMessage instanceof HTMLElement) {
        liveMessage.textContent = message;
      }
      setProgress({ percent: 100, text: "识别失败", detail: message });
    }
  };

  inputs.forEach((input) => {
    input.addEventListener("change", () => {
      inputs.forEach((other) => {
        if (other !== input) other.value = "";
      });
      sync();
    });
  });

  const readAllEntries = (reader) =>
    new Promise((resolve, reject) => {
      const entries = [];
      const readBatch = () => {
        reader.readEntries(
          (batch) => {
            if (!batch.length) {
              resolve(entries);
              return;
            }
            entries.push(...batch);
            readBatch();
          },
          reject,
        );
      };
      readBatch();
    });

  const fileFromEntry = (entry) =>
    new Promise((resolve, reject) => {
      entry.file(resolve, reject);
    });

  const filesFromEntry = async (entry) => {
    if (!entry) return [];
    if (entry.isFile) {
      return [await fileFromEntry(entry)];
    }
    if (!entry.isDirectory) return [];
    const entries = await readAllEntries(entry.createReader());
    const groups = await Promise.all(entries.map(filesFromEntry));
    return groups.flat();
  };

  const droppedImageFiles = async (dataTransfer) => {
    const items = Array.from(dataTransfer?.items || []);
    const entries = items.map((item) => (typeof item.webkitGetAsEntry === "function" ? item.webkitGetAsEntry() : null)).filter(Boolean);
    if (entries.length) {
      const groups = await Promise.all(entries.map(filesFromEntry));
      return groups.flat().filter(isImageFile);
    }
    return Array.from(dataTransfer?.files || []).filter(isImageFile);
  };

  clearButton?.addEventListener("click", () => {
    inputs.forEach((input) => {
      input.value = "";
    });
    sync();
  });

  form?.addEventListener("submit", async (event) => {
    const files = selectedFiles().filter(isImageFile);
    event.preventDefault();
    if (!files.length) {
      if (dropStatus instanceof HTMLElement) {
        dropStatus.textContent = "请先选择照片或拖入照片文件夹";
        dropStatus.classList.add("error");
      }
      return;
    }
    if (pollTimer) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
    setSubmitEnabled(false);
    status.textContent = `正在上传并识别 ${files.length} 张照片`;
    if (dropStatus instanceof HTMLElement) {
      dropStatus.textContent = "正在上传照片，请稍候";
      dropStatus.classList.remove("error");
    }
    if (runStatus instanceof HTMLElement) {
      runStatus.textContent = "正在上传照片...";
      runStatus.classList.add("active");
      runStatus.classList.remove("done", "error");
    }
    setProgress({ percent: 3, text: "正在上传照片", detail: `${files.length} 张照片` });
    showLiveResult();
    if (liveSummary instanceof HTMLElement) {
      liveSummary.textContent = "上传中";
    }
    if (liveMessage instanceof HTMLElement) {
      liveMessage.textContent = "照片正在上传到服务器。";
    }
    try {
      const response = await fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        headers: {
          Accept: "application/json",
          "X-Requested-With": "fetch",
        },
      });
      const data = await readJsonResponse(response, "提交失败");
      if (!response.ok || !data.ok) {
        throw new Error(data.error || "提交失败");
      }
      if (dropStatus instanceof HTMLElement) {
        dropStatus.textContent = "上传完成，开始逐张识别";
      }
      if (runStatus instanceof HTMLElement) {
        runStatus.textContent = "正在调用视觉模型并生成进度...";
      }
      setProgress({ percent: 8, text: "上传完成，等待模型识别", detail: "后台任务已开始" });
      await pollJob(data.status_url);
      pollTimer = window.setInterval(() => pollJob(data.status_url), 1000);
    } catch (error) {
      setSubmitEnabled(true);
      const message = error instanceof Error ? error.message : "提交失败";
      if (dropStatus instanceof HTMLElement) {
        dropStatus.textContent = message;
        dropStatus.classList.add("error");
      }
      if (runStatus instanceof HTMLElement) {
        runStatus.textContent = message;
        runStatus.classList.add("active", "error");
        runStatus.classList.remove("done");
      }
      setProgress({ percent: 100, text: "提交失败", detail: message });
    }
  });

  let dragDepth = 0;
  picker.addEventListener("dragenter", (event) => {
    if (!hasFileDrag(event)) return;
    event.preventDefault();
    dragDepth += 1;
    picker.classList.add("drag-over");
    if (dropStatus instanceof HTMLElement) {
      dropStatus.textContent = "松开即可选择照片";
    }
  });
  picker.addEventListener("dragover", (event) => {
    if (!hasFileDrag(event)) return;
    event.preventDefault();
    if (event.dataTransfer) {
      event.dataTransfer.dropEffect = "copy";
    }
  });
  picker.addEventListener("dragleave", (event) => {
    if (!hasFileDrag(event)) return;
    dragDepth -= 1;
    if (dragDepth > 0) return;
    picker.classList.remove("drag-over");
    sync();
  });
  picker.addEventListener("drop", async (event) => {
    if (!hasFileDrag(event) || !(photoInput instanceof HTMLInputElement)) return;
    event.preventDefault();
    dragDepth = 0;
    picker.classList.remove("drag-over");
    const files = event.dataTransfer ? await droppedImageFiles(event.dataTransfer) : [];
    if (!files.length || typeof DataTransfer === "undefined") {
      if (dropStatus instanceof HTMLElement) {
        dropStatus.textContent = "没有找到可用图片，请选择照片或拖入照片文件夹";
        dropStatus.classList.add("error");
      }
      return;
    }
    const transfer = new DataTransfer();
    files.forEach((file) => transfer.items.add(file));
    photoInput.files = transfer.files;
    sync();
  });

  sync();
});

document.querySelectorAll("[data-price-mode]").forEach((select) => {
  const form = select.closest("form");
  const rateField = form ? form.querySelector("[data-exchange-rate-field]") : null;
  const rateInput = form ? form.querySelector("[data-exchange-rate]") : null;
  const syncRateField = () => {
    const needsRate = select.value === "usd";
    if (rateField instanceof HTMLElement) {
      rateField.hidden = !needsRate;
    }
    if (rateInput instanceof HTMLInputElement) {
      rateInput.disabled = !needsRate;
      rateInput.required = needsRate;
    }
  };

  select.addEventListener("change", syncRateField);
  syncRateField();
});

document.querySelectorAll("form[data-submit-wait]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (form.dataset.submitInProgress === "true") {
      event.preventDefault();
      return;
    }
    form.dataset.submitInProgress = "true";

    const waitText = form.dataset.submitWaitText || "正在处理，请稍候...";
    const buttonText = form.dataset.submitWaitButtonText || waitText;
    const buttons = Array.from(form.querySelectorAll("button[type='submit']"));
    const message = form.querySelector("[data-submit-wait-message]");
    buttons.forEach((button) => {
      if (!(button instanceof HTMLButtonElement)) return;
      button.dataset.originalText = button.dataset.originalText || button.textContent || "";
      button.disabled = true;
      button.textContent = buttonText;
    });
    if (message instanceof HTMLElement) {
      message.textContent = waitText;
      message.classList.add("active");
      message.classList.remove("done", "error");
    }

    const resetAfter = Number.parseInt(form.dataset.submitWaitReset || "", 10);
    if (Number.isFinite(resetAfter) && resetAfter > 0) {
      window.setTimeout(() => {
        form.dataset.submitInProgress = "false";
        buttons.forEach((button) => {
          if (!(button instanceof HTMLButtonElement)) return;
          button.disabled = false;
          button.textContent = button.dataset.originalText || button.textContent;
        });
        if (message instanceof HTMLElement) {
          message.textContent = "已开始生成，请查看浏览器下载提示。";
          message.classList.remove("active", "error");
          message.classList.add("done");
        }
      }, resetAfter);
    }
  });
});

document.querySelectorAll("form[data-enter-navigation]").forEach((form) => {
  const focusableSelector = [
    "input:not([type='hidden']):not([type='checkbox']):not([disabled])",
    "select:not([disabled])",
    "textarea:not([disabled])",
    "button[type='submit']:not([disabled])",
  ].join(",");

  form.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.isComposing) return;
    if (event.metaKey || event.ctrlKey) {
      event.preventDefault();
      form.requestSubmit();
      return;
    }
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    if (!target.matches("input, select, textarea")) return;
    if (target instanceof HTMLTextAreaElement && event.shiftKey) return;

    event.preventDefault();
    const fields = Array.from(form.querySelectorAll(focusableSelector)).filter((field) => {
      return field instanceof HTMLElement && field.offsetParent !== null;
    });
    const index = fields.indexOf(target);
    const next = fields[index + 1];
    if (next instanceof HTMLElement) {
      next.focus();
      if (next instanceof HTMLInputElement) {
        next.select();
      }
    }
  });
});

const quickOeImageModal = document.querySelector("#quick-oe-image-modal");
const quickOeImageModalImg = document.querySelector("#quick-oe-image-modal-img");
const quickOeImageModalCaption = document.querySelector("#quick-oe-image-modal-caption");

const closeQuickOeImageModal = () => {
  if (!quickOeImageModal || !quickOeImageModalImg || !quickOeImageModalCaption) return;
  quickOeImageModal.classList.remove("open");
  quickOeImageModal.setAttribute("aria-hidden", "true");
  document.body.classList.remove("modal-open");
  quickOeImageModalImg.src = "";
  quickOeImageModalImg.alt = "";
  quickOeImageModalCaption.textContent = "";
};

document.querySelectorAll("[data-quick-oe-image]").forEach((link) => {
  link.addEventListener("click", (event) => {
    if (!quickOeImageModal || !quickOeImageModalImg || !quickOeImageModalCaption) return;
    event.preventDefault();
    const image = link.querySelector("img");
    quickOeImageModalImg.src = link.href;
    quickOeImageModalImg.alt = image?.alt || "产品图片";
    quickOeImageModalCaption.textContent = link.dataset.caption || "";
    quickOeImageModal.classList.add("open");
    quickOeImageModal.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");
  });
});

document.querySelectorAll("[data-close-quick-oe-image-modal]").forEach((element) => {
  element.addEventListener("click", closeQuickOeImageModal);
});

const downloadModal = document.querySelector("#download-excel-modal");

const closeDownloadModal = () => {
  if (!downloadModal) return;
  downloadModal.classList.remove("open");
  downloadModal.setAttribute("aria-hidden", "true");
  document.body.classList.remove("modal-open");
};

document.querySelectorAll("[data-open-download-modal]").forEach((button) => {
  button.addEventListener("click", () => {
    if (!downloadModal) return;
    downloadModal.classList.add("open");
    downloadModal.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");
    const select = downloadModal.querySelector("[data-price-mode]");
    if (select instanceof HTMLElement) {
      select.focus();
    }
  });
});

document.querySelectorAll("[data-close-download-modal]").forEach((element) => {
  element.addEventListener("click", closeDownloadModal);
});

const shippingModals = Array.from(document.querySelectorAll("#shipping-template-select-modal, #shipping-template-menu-modal, #shipping-template-upload-modal, #shipping-template-batch-modal"));

const closeShippingModals = () => {
  shippingModals.forEach((modal) => {
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
  });
  document.body.classList.remove("modal-open");
};

const openShippingModal = (modal) => {
  if (!(modal instanceof HTMLElement)) return;
  closeShippingModals();
  modal.classList.add("open");
  modal.setAttribute("aria-hidden", "false");
  document.body.classList.add("modal-open");
  const focusTarget = modal.querySelector("select, input, textarea, button:not([data-close-shipping-modal])");
  if (focusTarget instanceof HTMLElement) {
    focusTarget.focus();
  }
};

document.querySelector("[data-open-shipping-template-select-modal]")?.addEventListener("click", () => {
  const fileInput = document.querySelector("#shipping-data-input");
  if (fileInput instanceof HTMLInputElement && (!fileInput.files || fileInput.files.length === 0)) {
    const picker = fileInput.closest(".file-picker");
    const dropStatus = picker?.querySelector("[data-file-drop-status]");
    if (dropStatus instanceof HTMLElement) {
      dropStatus.textContent = "请先选择或拖入发货数据文件。";
      dropStatus.classList.add("error");
    }
    return;
  }
  openShippingModal(document.querySelector("#shipping-template-select-modal"));
});

document.querySelector("[data-open-shipping-template-menu-modal]")?.addEventListener("click", () => {
  openShippingModal(document.querySelector("#shipping-template-menu-modal"));
});

document.querySelectorAll("[data-open-shipping-template-action-modal]").forEach((button) => {
  button.addEventListener("click", () => {
    const target = button.dataset.openShippingTemplateActionModal;
    openShippingModal(document.querySelector(`#shipping-template-${target}-modal`));
  });
});

document.querySelectorAll("[data-close-shipping-modal]").forEach((element) => {
  element.addEventListener("click", closeShippingModals);
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && quickOeImageModal?.classList.contains("open")) {
    closeQuickOeImageModal();
  }
  if (event.key === "Escape" && downloadModal?.classList.contains("open")) {
    closeDownloadModal();
  }
  if (event.key === "Escape" && shippingModals.some((modal) => modal.classList.contains("open"))) {
    closeShippingModals();
  }
});

document.querySelectorAll("[data-purchase-contract-form]").forEach((form) => {
  const body = form.querySelector("[data-purchase-rows]");
  const addButton = form.querySelector("[data-add-purchase-row]");
  if (!(body instanceof HTMLTableSectionElement) || !(addButton instanceof HTMLButtonElement)) return;
  const isSalesContract = form.dataset.contractKind === "sales";
  const productCache = new Map();

  const totalQuantityCell = form.querySelector("[data-purchase-total-quantity]");
  const totalAmountCell = form.querySelector("[data-purchase-total-amount]");
  const totalSmallCell = form.querySelector("[data-purchase-total-small]");
  const totalUpperCell = form.querySelector("[data-purchase-total-upper]");
  const buyerNameInput = form.querySelector('[name="buyer_name"]');
  const supplierNameInput = form.querySelector('[name="supplier_name"]');
  const buyerSignName = form.querySelector("[data-buyer-sign-name]");
  const supplierSignName = form.querySelector("[data-supplier-sign-name]");
  const submitButton = form.querySelector("[data-purchase-submit-button]");
  const submitButtonText = submitButton instanceof HTMLButtonElement ? submitButton.textContent : "生成 PDF";
  const feedback = form.querySelector("[data-purchase-feedback]");
  const confirmModal = document.querySelector("[data-purchase-confirm-modal]");
  const confirmSubmitButton = confirmModal?.querySelector("[data-purchase-confirm-submit]");
  const confirmCancelButtons = confirmModal?.querySelectorAll("[data-purchase-confirm-cancel]");
  let confirmedSubmit = false;

  const setFeedback = (text) => {
    if (!(feedback instanceof HTMLElement)) return;
    feedback.textContent = text;
    feedback.hidden = false;
  };

  const openConfirm = () => {
    if (!(confirmModal instanceof HTMLElement)) return;
    confirmModal.classList.add("open");
    confirmModal.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");
    if (confirmSubmitButton instanceof HTMLElement) {
      confirmSubmitButton.focus();
    }
  };

  const closeConfirm = () => {
    if (!(confirmModal instanceof HTMLElement)) return;
    confirmModal.classList.remove("open");
    confirmModal.setAttribute("aria-hidden", "true");
    document.body.classList.remove("modal-open");
  };

  const parseNumber = (value) => {
    const number = Number.parseFloat(String(value || "").replace(/,/g, ""));
    return Number.isFinite(number) ? number : null;
  };

  const formatMoney = (value) => value.toLocaleString("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

  const formatQuantity = (value) => {
    if (!value) return "";
    return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(4))).replace(/\.0+$/, "");
  };

  const rmbUpper = (value) => {
    const digits = "零壹贰叁肆伍陆柒捌玖";
    const units = ["", "拾", "佰", "仟"];
    const sections = ["", "万", "亿", "兆"];
    const totalCents = Math.max(0, Math.round(value * 100));
    let yuan = Math.floor(totalCents / 100);
    const fraction = totalCents % 100;

    const sectionToUpper = (section) => {
      let result = "";
      let zeroPending = false;
      for (let index = 0; index < 4; index += 1) {
        const digit = section % 10;
        if (digit) {
          if (zeroPending) {
            result = digits[0] + result;
            zeroPending = false;
          }
          result = digits[digit] + units[index] + result;
        } else if (result) {
          zeroPending = true;
        }
        section = Math.floor(section / 10);
      }
      return result;
    };

    let yuanText = "零元";
    if (yuan > 0) {
      const parts = [];
      let sectionIndex = 0;
      let zeroPending = false;
      while (yuan > 0) {
        const section = yuan % 10000;
        if (section) {
          const prefix = zeroPending && parts.length ? digits[0] : "";
          parts.push(prefix + sectionToUpper(section) + sections[sectionIndex]);
          zeroPending = section < 1000;
        } else if (parts.length) {
          zeroPending = true;
        }
        yuan = Math.floor(yuan / 10000);
        sectionIndex += 1;
      }
      yuanText = parts.reverse().join("") + "元";
    }

    if (!fraction) return `${yuanText}整`;
    const jiao = Math.floor(fraction / 10);
    const fen = fraction % 10;
    let fractionText = "";
    if (jiao) {
      fractionText += `${digits[jiao]}角`;
    } else if (totalCents >= 100) {
      fractionText += digits[0];
    }
    if (fen) {
      fractionText += `${digits[fen]}分`;
    }
    return yuanText + fractionText;
  };

  const syncRows = () => {
    const rows = Array.from(body.querySelectorAll("tr"));
    let totalQuantity = 0;
    let totalAmount = 0;
    rows.forEach((row, index) => {
      const indexCell = row.querySelector("[data-purchase-index]");
      if (indexCell instanceof HTMLElement) {
        indexCell.textContent = String(index + 1);
      }
      ["product_code[]", "quantity[]", "unit_price[]"].forEach((name) => {
        const input = row.querySelector(`[name="${name}"]`);
        if (input instanceof HTMLInputElement) {
          input.required = index === 0;
        }
      });
      const quantity = parseNumber(row.querySelector('[name="quantity[]"]')?.value);
      const unitPrice = parseNumber(row.querySelector('[name="unit_price[]"]')?.value);
      const amountCell = row.querySelector("[data-purchase-amount]");
      if (quantity !== null && unitPrice !== null && quantity >= 0 && unitPrice >= 0) {
        const amount = Math.round(quantity * unitPrice * 100) / 100;
        totalQuantity += quantity;
        totalAmount += amount;
        if (amountCell instanceof HTMLElement) {
          amountCell.textContent = formatMoney(amount);
        }
      } else if (amountCell instanceof HTMLElement) {
        amountCell.textContent = "";
      }
    });
    if (totalQuantityCell instanceof HTMLElement) {
      totalQuantityCell.textContent = formatQuantity(totalQuantity);
    }
    if (totalAmountCell instanceof HTMLElement) {
      totalAmountCell.textContent = totalAmount ? formatMoney(totalAmount) : "";
    }
    if (totalSmallCell instanceof HTMLElement) {
      totalSmallCell.textContent = formatMoney(totalAmount);
    }
    if (totalUpperCell instanceof HTMLElement) {
      totalUpperCell.textContent = rmbUpper(totalAmount);
    }
  };

  const createRow = () => {
    const row = document.createElement("tr");
    row.innerHTML = [
      '<td class="purchase-row-index" data-purchase-index></td>',
      '<td><input name="product_code[]" data-purchase-bld></td>',
      isSalesContract ? '<td><input name="customer_code[]" data-customer-code></td>' : "",
      '<td><input name="oe_no[]" data-purchase-oe></td>',
      '<td><input name="product_name[]" data-purchase-name></td>',
      '<td><input name="models[]" data-purchase-models></td>',
      '<td><input name="quantity[]" inputmode="decimal"></td>',
      '<td><input name="unit_price[]" inputmode="decimal"></td>',
      '<td class="purchase-amount-cell" data-purchase-amount></td>',
      '<td><input name="item_note[]"></td>',
      '<td><input name="delivery_date[]"></td>',
      '<td><button class="link-button danger-link" type="button" data-remove-purchase-row>删除</button></td>',
    ].join("");
    return row;
  };

  const setValue = (row, selector, value) => {
    const input = row.querySelector(selector);
    if (input instanceof HTMLInputElement) {
      input.value = value || "";
    }
  };

  const applyProduct = (row, product) => {
    if (!product || !product.found) {
      setValue(row, "[data-purchase-oe]", "");
      setValue(row, "[data-purchase-name]", "");
      setValue(row, "[data-purchase-models]", "");
      return;
    }
    setValue(row, "[data-purchase-bld]", product.bld_no);
    setValue(row, "[data-purchase-oe]", product.oe_no);
    setValue(row, "[data-purchase-name]", product.product_name);
    setValue(row, "[data-purchase-models]", product.models);
    const unitPriceInput = row.querySelector('[name="unit_price[]"]');
    if (isSalesContract && unitPriceInput instanceof HTMLInputElement && !unitPriceInput.value && product.price_cny !== null && product.price_cny !== undefined) {
      unitPriceInput.value = String(product.price_cny);
      syncRows();
    }
  };

  const lookupProduct = async (input) => {
    const bld = input.value.trim();
    const row = input.closest("tr");
    if (!bld || !(row instanceof HTMLTableRowElement)) return;
    const cacheKey = bld.toUpperCase();
    if (productCache.has(cacheKey)) {
      applyProduct(row, productCache.get(cacheKey));
      return;
    }
    try {
      const response = await fetch(`/purchase-contracts/product-lookup?bld=${encodeURIComponent(bld)}`, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) return;
      const product = await response.json();
      productCache.set(cacheKey, product);
      applyProduct(row, product);
    } catch (_error) {
      // 留在手动填写状态。
    }
  };

  addButton.addEventListener("click", () => {
    const row = createRow();
    body.appendChild(row);
    syncRows();
    row.querySelector("input")?.focus();
  });

  body.addEventListener("click", (event) => {
    const button = event.target instanceof Element ? event.target.closest("[data-remove-purchase-row]") : null;
    if (!button) return;
    const row = button.closest("tr");
    row?.remove();
    if (!body.querySelector("tr")) {
      body.appendChild(createRow());
    }
    syncRows();
  });

  body.addEventListener("input", (event) => {
    const input = event.target;
    if (input instanceof HTMLInputElement && (input.name === "quantity[]" || input.name === "unit_price[]")) {
      syncRows();
    }
  });

  body.addEventListener("change", (event) => {
    const input = event.target;
    if (input instanceof HTMLInputElement && input.matches("[data-purchase-bld]")) {
      lookupProduct(input);
    }
  });

  body.addEventListener("blur", (event) => {
    const input = event.target;
    if (input instanceof HTMLInputElement && input.matches("[data-purchase-bld]")) {
      lookupProduct(input);
    }
  }, true);

  form.querySelectorAll(".paper-textarea").forEach((textarea) => {
    if (!(textarea instanceof HTMLTextAreaElement)) return;
    const fit = () => {
      textarea.style.height = "auto";
      textarea.style.height = `${textarea.scrollHeight}px`;
    };
    textarea.addEventListener("input", fit);
    fit();
  });

  if (buyerNameInput instanceof HTMLInputElement && buyerSignName instanceof HTMLElement) {
    const syncBuyerSignName = () => {
      buyerSignName.textContent = buyerNameInput.value;
    };
    buyerNameInput.addEventListener("input", syncBuyerSignName);
    syncBuyerSignName();
  }

  if (supplierNameInput instanceof HTMLInputElement && supplierSignName instanceof HTMLElement) {
    const syncSupplierSignName = () => {
      supplierSignName.textContent = supplierNameInput.value;
    };
    supplierNameInput.addEventListener("input", syncSupplierSignName);
    syncSupplierSignName();
  }

  confirmCancelButtons?.forEach((button) => {
    button.addEventListener("click", closeConfirm);
  });

  confirmSubmitButton?.addEventListener("click", () => {
    confirmedSubmit = true;
    closeConfirm();
    form.requestSubmit();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && confirmModal instanceof HTMLElement && confirmModal.classList.contains("open")) {
      closeConfirm();
    }
  });

  form.addEventListener("submit", (event) => {
    if (!confirmedSubmit) {
      event.preventDefault();
      if (form.reportValidity()) {
        openConfirm();
      }
      return;
    }
    if (submitButton instanceof HTMLButtonElement) {
      submitButton.disabled = true;
      submitButton.textContent = "正在生成...";
    }
    setFeedback("已确认生成 PDF，浏览器会开始下载；生成记录可在页面底部「最近合同」查看。");
    window.setTimeout(() => {
      confirmedSubmit = false;
      if (submitButton instanceof HTMLButtonElement) {
        submitButton.disabled = false;
        submitButton.textContent = submitButtonText || "生成 PDF";
      }
    }, 6000);
  });

  syncRows();
});
