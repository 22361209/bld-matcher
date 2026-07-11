document.querySelectorAll(".shipment-folder-picker").forEach((picker) => {
  const photoInput = picker.querySelector(".shipment-file-input");
  const status = picker.querySelector("[data-shipment-file-status]");
  const clearButton = picker.querySelector("[data-clear-shipment-files]");
  const dropStatus = picker.querySelector("[data-file-drop-status]");
  const form = picker.closest("[data-shipment-form]");
  const submitButton = form?.querySelector("[data-shipment-submit]");
  const cancelButton = form?.querySelector("[data-shipment-cancel]");
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
  let currentStatusUrl = form?.dataset.activeShipmentStatusUrl || "";
  let currentCancelUrl = form?.dataset.activeShipmentCancelUrl || "";

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
  const setCancelVisible = (visible) => {
    if (cancelButton instanceof HTMLButtonElement) {
      cancelButton.hidden = !visible;
      cancelButton.disabled = false;
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
      setProgress({ percent: 0, text: "等待开始", detail: "" });
      progressPanel.hidden = true;
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
      setCancelVisible(job.status === "queued" || job.status === "running");
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
        if (liveMessage instanceof HTMLElement) {
          liveMessage.textContent = "识别完成，可以下载结果。";
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
        setCancelVisible(false);
        return false;
      }
      if (job.status === "error") {
        throw new Error(job.error || job.message || "识别失败");
      }
      if (job.status === "failed") {
        throw new Error(job.error || job.message || "识别失败");
      }
      if (job.status === "cancelled") {
        window.clearInterval(pollTimer);
        pollTimer = null;
        setSubmitEnabled(true);
        setCancelVisible(false);
        status.textContent = "任务已取消";
        if (runStatus instanceof HTMLElement) {
          runStatus.textContent = "任务已取消";
          runStatus.classList.add("active");
          runStatus.classList.remove("done", "error");
        }
        if (liveSummary instanceof HTMLElement) liveSummary.textContent = "任务已取消";
        if (liveMessage instanceof HTMLElement) liveMessage.textContent = "可以重新选择照片并提交。";
        setProgress({ percent, text: "任务已取消", detail: summary });
        return false;
      }
      return true;
    } catch (error) {
      window.clearInterval(pollTimer);
      pollTimer = null;
      setSubmitEnabled(true);
      setCancelVisible(false);
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
      return false;
    }
  };

  cancelButton?.addEventListener("click", async () => {
    if (!currentCancelUrl || !(cancelButton instanceof HTMLButtonElement)) return;
    cancelButton.disabled = true;
    const csrfToken = form?.querySelector("input[name='csrf_token']")?.value || "";
    try {
      const response = await fetch(currentCancelUrl, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "X-Requested-With": "fetch",
          "X-CSRF-Token": csrfToken,
        },
      });
      const data = await readJsonResponse(response, "取消任务失败");
      if (!response.ok || !data.ok) throw new Error(data.error || "取消任务失败");
      if (runStatus instanceof HTMLElement) runStatus.textContent = "已请求取消，等待当前照片处理结束";
      if (currentStatusUrl) await pollJob(currentStatusUrl);
    } catch (error) {
      cancelButton.disabled = false;
      if (runStatus instanceof HTMLElement) {
        runStatus.textContent = error instanceof Error ? error.message : "取消任务失败";
        runStatus.classList.add("active", "error");
      }
    }
  });

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
      currentStatusUrl = data.status_url || "";
      currentCancelUrl = data.cancel_url || "";
      if (data.job_id) {
        const pageUrl = new URL(window.location.href);
        pageUrl.searchParams.set("job_id", data.job_id);
        window.history.replaceState({}, "", pageUrl);
      }
      setProgress({ percent: 8, text: "上传完成，等待模型识别", detail: "后台任务已开始" });
      const active = await pollJob(currentStatusUrl);
      if (active) pollTimer = window.setInterval(() => pollJob(currentStatusUrl), 1000);
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
  if (currentStatusUrl) {
    setSubmitEnabled(false);
    setProgress({ percent: 0, text: "正在恢复任务进度", detail: "" });
    pollJob(currentStatusUrl).then((active) => {
      if (active) pollTimer = window.setInterval(() => pollJob(currentStatusUrl), 1000);
    });
  }
});
