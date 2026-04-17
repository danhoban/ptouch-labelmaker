(function () {
  const config = window.PTOUCH_CONFIG || {};
  const defaultFontKey = config.defaultFontKey || 'auto';
  const defaultBorderStyle = config.defaultBorderStyle || 'none';
  const defaultIconPath = normalizeIconPath(config.defaultIcon);
  const iconMinHeight = Number(config.iconMinHeight) || 16;
  const qrMinSize = Number(config.qrMinSize) || 24;
  const ICON_PADDING = 12;
  const QR_PADDING = 12;
  const ICON_DEFAULT_RATIO = 0.85;
  const QR_DEFAULT_RATIO = 0.85;

  let currentFileId = null;
  let printerAvailable = false;
  let maxHeight = null;
  let hasError = false;
  let errorMessage = null;
  let currentIconPath = defaultIconPath;
  let currentIconSize = iconMinHeight;
  let currentQrSize = qrMinSize;
  let iconModalIsOpen = false;
  let iconSizeUserSet = false;
  let qrSizeUserSet = false;

  const $ = (id) => document.getElementById(id);

  const elements = {
    labelText: $('labelText'),
    labelUrl: $('labelUrl'),
    fontSelect: $('fontFamily'),
    borderSelect: $('borderStyle'),
    fontSize: $('fontSize'),
    qrSizeInput: $('qrSize'),
    qrSizeValue: $('qrSizeValue'),
    previewBtn: $('previewBtn'),
    printBtn: $('printBtn'),
    refreshBtn: $('refreshBtn'),
    statusBadge: $('availBadge'),
    statusError: $('errorMsg'),
    details: $('details'),
    hint: $('hint'),
    previewPane: $('previewPane'),
    iconDisplayLabel: $('iconDisplayLabel'),
    iconPath: $('iconPath'),
    iconPreview: $('iconPreview'),
    iconPreviewPlaceholder: $('iconPreviewPlaceholder'),
    openIconPicker: $('openIconPicker'),
    clearIcon: $('clearIcon'),
    iconModal: $('iconModal'),
    iconModalClose: $('iconModalClose'),
    iconDirList: $('iconDirList'),
    iconGrid: $('iconGrid'),
    iconBreadcrumbs: $('iconBreadcrumbs'),
    iconEmptyState: $('iconEmptyState'),
    iconSizeInput: $('iconSize'),
    iconSizeValue: $('iconSizeValue'),
    tabLocal: $('tabLocal'),
    tabIconify: $('tabIconify'),
    panelLocal: $('panelLocal'),
    panelIconify: $('panelIconify'),
    iconSearch: $('iconSearch'),
    iconSearchResults: $('iconSearchResults'),
    iconSearchEmpty: $('iconSearchEmpty'),
    iconBrowsePanel: $('iconBrowsePanel'),
    iconifySearch: $('iconifySearch'),
    iconifySearchBtn: $('iconifySearchBtn'),
    iconifyGrid: $('iconifyGrid'),
    iconifyState: $('iconifyState'),
    libraryCard: $('libraryCard'),
    libraryList: $('libraryList'),
    historyCard: $('historyCard'),
    historyList: $('historyList'),
    clearHistoryBtn: $('clearHistoryBtn'),
    labelWidth: $('labelWidth'),
    diagnosticsBtn: $('diagnosticsBtn'),
    diagnosticsModal: $('diagnosticsModal'),
    diagnosticsModalClose: $('diagnosticsModalClose'),
  };
  elements.iconModalBackdrop = elements.iconModal ? elements.iconModal.querySelector('[data-close]') : null;

  let localSearchDebounce = null;

  function normalizeIconPath(value) {
    if (!value || value === 'none') return '';
    return String(value).trim();
  }

  function escapeHtml(value) {
    return value ? value.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c])) : value;
  }

  function encodePath(path) {
    return path.split('/').map(encodeURIComponent).join('/');
  }

  function iconPathToUrl(path) {
    const normalized = normalizeIconPath(path);
    if (!normalized) return null;
    return `/static/icons/${encodePath(normalized)}`;
  }

  function displayNameFromPath(path) {
    const normalized = normalizeIconPath(path);
    if (!normalized) return 'No icon selected';
    const parts = normalized.split('/');
    return parts[parts.length - 1] || normalized;
  }

  function updateIconSizeDisplay() {
    if (!elements.iconSizeValue || !elements.iconSizeInput) return;
    elements.iconSizeValue.textContent = `${elements.iconSizeInput.value} px`;
  }

  function updateQrSizeDisplay() {
    if (!elements.qrSizeValue || !elements.qrSizeInput) return;
    elements.qrSizeValue.textContent = `${elements.qrSizeInput.value} px`;
  }

  function computeDefaultIconSize() {
    const height = maxHeight || 0;
    const available = Math.max(0, height - ICON_PADDING * 2);
    if (available <= 0) return iconMinHeight;
    const target = Math.round(height * ICON_DEFAULT_RATIO);
    return Math.min(available, Math.max(iconMinHeight, target));
  }

  function computeDefaultQrSize() {
    const height = maxHeight || 0;
    const available = Math.max(0, height - 2 * Math.max(2, Math.floor(QR_PADDING / 3)));
    if (available <= 0) return qrMinSize;
    const target = Math.round(height * QR_DEFAULT_RATIO);
    return Math.min(available, Math.max(qrMinSize, target));
  }

  function updateIconSliderRange() {
    if (!elements.iconSizeInput) return;
    const available = Math.max(0, (maxHeight || 0) - ICON_PADDING * 2);
    let locked = false;
    if (available <= 0) {
      elements.iconSizeInput.min = String(iconMinHeight);
      elements.iconSizeInput.max = String(iconMinHeight);
      currentIconSize = iconMinHeight;
      elements.iconSizeInput.value = String(currentIconSize);
      locked = true;
    }
    if (!locked) {
      const sliderMin = Math.max(1, Math.min(iconMinHeight, available));
      const sliderMax = Math.max(sliderMin, available);
      elements.iconSizeInput.min = String(sliderMin);
      elements.iconSizeInput.max = String(sliderMax);
      if (!currentIconSize || currentIconSize < sliderMin || currentIconSize > sliderMax) {
        currentIconSize = Math.min(Math.max(sliderMin, computeDefaultIconSize()), sliderMax);
      }
      elements.iconSizeInput.value = String(currentIconSize);
    }
    elements.iconSizeInput.dataset.locked = locked ? 'true' : 'false';
    elements.iconSizeInput.disabled = locked || !currentIconPath || !printerAvailable || hasError;
    updateIconSizeDisplay();
  }

  function updateQrSliderRange() {
    if (!elements.qrSizeInput) return;
    const available = Math.max(0, (maxHeight || 0) - 2 * Math.max(2, Math.floor(QR_PADDING / 3)));
    let locked = false;
    if (available <= 0) {
      elements.qrSizeInput.min = String(qrMinSize);
      elements.qrSizeInput.max = String(qrMinSize);
      currentQrSize = qrMinSize;
      elements.qrSizeInput.value = String(currentQrSize);
      locked = true;
    }
    if (!locked) {
      const sliderMin = Math.max(1, Math.min(qrMinSize, available));
      const sliderMax = Math.max(sliderMin, available);
      elements.qrSizeInput.min = String(sliderMin);
      elements.qrSizeInput.max = String(sliderMax);
      if (!currentQrSize || currentQrSize < sliderMin || currentQrSize > sliderMax) {
        currentQrSize = Math.min(Math.max(sliderMin, computeDefaultQrSize()), sliderMax);
      }
      elements.qrSizeInput.value = String(currentQrSize);
    }
    elements.qrSizeInput.dataset.locked = locked ? 'true' : 'false';
    elements.qrSizeInput.disabled = locked || !printerAvailable || hasError;
    updateQrSizeDisplay();
  }

  function updateIconUi(path, options = {}) {
    const normalized = normalizeIconPath(path);
    const url = options.url !== undefined ? options.url : iconPathToUrl(normalized);
    currentIconPath = normalized;

    if (elements.iconPath) elements.iconPath.value = normalized;
    if (elements.iconDisplayLabel) elements.iconDisplayLabel.textContent = displayNameFromPath(normalized);

    if (elements.iconPreview) {
      if (url) {
        elements.iconPreview.src = url;
        elements.iconPreview.hidden = false;
        if (elements.iconPreviewPlaceholder) elements.iconPreviewPlaceholder.hidden = true;
      } else {
        elements.iconPreview.src = '';
        elements.iconPreview.hidden = true;
        if (elements.iconPreviewPlaceholder) elements.iconPreviewPlaceholder.hidden = false;
      }
    }

    if (normalized && !iconSizeUserSet) {
      currentIconSize = computeDefaultIconSize();
    }

    updateIconSliderRange();
    if (iconModalIsOpen) highlightActiveIconTile();
  }

  updateIconUi(currentIconPath);
  updateQrSliderRange();

  if (elements.iconSizeInput) {
    elements.iconSizeInput.addEventListener('input', () => {
      currentIconSize = Number(elements.iconSizeInput.value) || currentIconSize || iconMinHeight;
      updateIconSizeDisplay();
      iconSizeUserSet = true;
    });
    updateIconSizeDisplay();
  }

  if (elements.qrSizeInput) {
    elements.qrSizeInput.addEventListener('input', () => {
      currentQrSize = Number(elements.qrSizeInput.value) || currentQrSize || qrMinSize;
      updateQrSizeDisplay();
      qrSizeUserSet = true;
    });
    updateQrSizeDisplay();
  }

  function setInputsDisabled(disabled) {
    if (elements.labelText) elements.labelText.disabled = disabled;
    if (elements.labelUrl) elements.labelUrl.disabled = disabled;
    if (elements.fontSelect) {
      const hasEnabledOption = Array.from(elements.fontSelect.options || []).some((opt) => !opt.disabled);
      elements.fontSelect.disabled = disabled || !hasEnabledOption;
    }
    if (elements.borderSelect) elements.borderSelect.disabled = disabled;
    if (elements.iconSizeInput) {
      const locked = elements.iconSizeInput.dataset.locked === 'true';
      elements.iconSizeInput.disabled = disabled || locked || !currentIconPath;
    }
    if (elements.qrSizeInput) {
      const locked = elements.qrSizeInput.dataset.locked === 'true';
      elements.qrSizeInput.disabled = disabled || locked;
    }
    if (elements.openIconPicker) elements.openIconPicker.disabled = disabled;
    if (elements.clearIcon) elements.clearIcon.disabled = disabled;
    if (elements.fontSize) elements.fontSize.disabled = disabled;
    if (elements.previewBtn) elements.previewBtn.disabled = disabled;
    if (elements.printBtn) elements.printBtn.disabled = disabled || !currentFileId;
    if (disabled && iconModalIsOpen) closeIconModal();
  }

  async function fetchStatus() {
    const res = await fetch('/api/printer_status');
    const data = await res.json();
    printerAvailable = !!data.available;
    maxHeight = data.max_tape_px || data.max_printer_px || 128;
    hasError = !!data.has_error;
    errorMessage = data.error_message || null;

    if (elements.statusBadge) {
      if (!printerAvailable) {
        elements.statusBadge.className = 'badge err';
        elements.statusBadge.textContent = 'Printer not found';
      } else if (hasError) {
        elements.statusBadge.className = 'badge err';
        elements.statusBadge.textContent = 'Printer error';
      } else {
        elements.statusBadge.className = 'badge ok';
        elements.statusBadge.textContent = 'Printer ready';
      }
    }

    if (elements.statusError) {
      if (!printerAvailable) {
        elements.statusError.style.display = 'block';
        elements.statusError.innerHTML = '<span class="badge err">Turn the printer on and set the switch to E, then click Refresh.</span>';
      } else if (hasError) {
        elements.statusError.style.display = 'block';
        elements.statusError.innerHTML = `<span class="badge err">${escapeHtml(errorMessage)}</span>`;
      } else {
        elements.statusError.style.display = 'none';
        elements.statusError.textContent = '';
      }
    }

    if (elements.details) {
      const lines = [];
      if (data.model) lines.push(`Model: ${data.model}`);
      if (data.max_tape_px) lines.push(`Max tape height: ${data.max_tape_px}px`);
      if (data.media_width) lines.push(`Media width: ${data.media_width}`);
      if (data.tape_color) lines.push(`Tape: ${data.tape_color}`);
      if (data.text_color) lines.push(`Text: ${data.text_color}`);
      if (data.error_code) lines.push(`Error code: ${data.error_code}`);
      const diagnosticsText = (lines.length ? `${lines.join('\n')}\n\n` : '') + (data.raw || '');
      elements.details.textContent = diagnosticsText || 'No diagnostic information available.';
      if (elements.diagnosticsBtn) elements.diagnosticsBtn.hidden = !diagnosticsText;
    }

    if (!iconSizeUserSet) {
      currentIconSize = computeDefaultIconSize();
    }
    if (!qrSizeUserSet) {
      currentQrSize = computeDefaultQrSize();
    }

    updateIconSliderRange();
    updateQrSliderRange();
    setInputsDisabled(!printerAvailable || hasError);

    if (elements.hint) {
      if (!printerAvailable) {
        elements.hint.textContent = 'Turn the printer on and set the switch to E, then click Refresh.';
      } else if (hasError) {
        elements.hint.textContent = `Fix the printer issue: ${errorMessage}`;
      } else {
        elements.hint.textContent = `Output height will be clamped to ${maxHeight}px`;
      }
    }

    updateIconUi(currentIconPath);
  }

  async function doPreview() {
    const text = elements.labelText ? elements.labelText.value : '';
    const url = elements.labelUrl ? elements.labelUrl.value : '';
    const fontSize = parseInt((elements.fontSize ? elements.fontSize.value : '24') || '24', 10);
    const fontKey = elements.fontSelect ? (elements.fontSelect.value || defaultFontKey) : defaultFontKey;
    const borderStyle = elements.borderSelect ? (elements.borderSelect.value || defaultBorderStyle) : defaultBorderStyle;

    if (elements.iconSizeInput) currentIconSize = Number(elements.iconSizeInput.value) || currentIconSize || iconMinHeight;
    if (elements.qrSizeInput) currentQrSize = Number(elements.qrSizeInput.value) || currentQrSize || qrMinSize;

    const iconKey = elements.iconPath ? normalizeIconPath(elements.iconPath.value) : '';

    const labelWidthRaw = elements.labelWidth ? elements.labelWidth.value.trim() : '';
    const labelWidthMm = labelWidthRaw !== '' ? parseFloat(labelWidthRaw) : null;

    const res = await fetch('/api/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text,
        url,
        font_size: fontSize,
        qr_size: currentQrSize,
        font: fontKey,
        border_style: borderStyle,
        icon: iconKey,
        icon_size: currentIconSize,
        label_width_mm: labelWidthMm,
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      if (elements.previewPane) {
        elements.previewPane.innerHTML = `<span class="muted">${escapeHtml(data.error || 'Failed to generate')}</span>`;
      }
      currentFileId = null;
      if (elements.printBtn) elements.printBtn.disabled = true;
      return;
    }

    currentFileId = data.file_id;
    const imgUrl = `/preview/${currentFileId}.png?ts=${Date.now()}`;
    const notices = [];

    if (elements.fontSelect && data.font_key && fontKey && data.font_key !== fontKey) {
      const match = Array.from(elements.fontSelect.options).find((opt) => opt.value === data.font_key);
      const label = match ? match.textContent : data.font_key;
      notices.push(`Font fallback → ${escapeHtml((label || '').trim())}`);
    }

    if (elements.borderSelect && data.border_style && borderStyle && data.border_style !== borderStyle) {
      const match = Array.from(elements.borderSelect.options).find((opt) => opt.value === data.border_style);
      const label = match ? match.textContent : data.border_style;
      notices.push(`Border fallback → ${escapeHtml((label || '').trim())}`);
    }

    if (data.font_size && data.font_size !== fontSize) {
      notices.push(`Font size reduced to ${data.font_size}px to fit tape`);
    }

    const serverIconPath = normalizeIconPath(data.icon);
    if (serverIconPath !== iconKey) {
      if (serverIconPath || iconKey) notices.push(`Icon fallback → ${escapeHtml(displayNameFromPath(serverIconPath))}`);
      updateIconUi(serverIconPath, { url: iconPathToUrl(serverIconPath) });
    }

    if (elements.iconSizeInput) {
      const sliderMin = Number(elements.iconSizeInput.min) || iconMinHeight;
      const sliderMax = Number(elements.iconSizeInput.max) || sliderMin;
      const iconSizeFromServer = Number(data.icon_size) || currentIconSize;
      currentIconSize = Math.min(Math.max(sliderMin, iconSizeFromServer), sliderMax);
      elements.iconSizeInput.value = String(currentIconSize);
      updateIconSizeDisplay();
    }

    if (elements.qrSizeInput) {
      const sliderMin = Number(elements.qrSizeInput.min) || qrMinSize;
      const sliderMax = Number(elements.qrSizeInput.max) || sliderMin;
      const qrSizeFromServer = Number(data.qr_size) || currentQrSize;
      currentQrSize = Math.min(Math.max(sliderMin, qrSizeFromServer), sliderMax);
      elements.qrSizeInput.value = String(currentQrSize);
      updateQrSizeDisplay();
    }

    const noticeHtml = notices.length ? `<div class="muted" style="margin-bottom:6px">${notices.join('<br>')}</div>` : '';
    if (elements.previewPane) {
      elements.previewPane.innerHTML = `<div class="muted" style="margin-bottom:6px">${data.width}×${data.height}px preview (1‑bit B/W)</div>${noticeHtml}<img src="${imgUrl}" alt="preview" />`;
      const previewImg = elements.previewPane.querySelector('img');
      if (previewImg) {
        previewImg.addEventListener('mouseenter', () => showHoverCard(previewImg, `/preview/${currentFileId}.png`, Date.now() / 1000));
        previewImg.addEventListener('mouseleave', hideHoverCard);
      }
    }
    if (elements.printBtn) elements.printBtn.disabled = (!printerAvailable) || hasError;
  }

  // ── History ──────────────────────────────────────────────────────────────

  function historyDisplayLabel(entry) {
    if (entry.name) return entry.name;
    const parts = [];
    const firstLine = (entry.text || '').split('\n')[0].trim();
    if (firstLine) parts.push(firstLine);
    if (entry.icon) {
      const iconName = entry.icon.split('/').pop().replace(/\.[^.]+$/, '');
      parts.push(`[${iconName}]`);
    }
    if (!parts.length && entry.url) parts.push(entry.url);
    return parts.join('  ') || '(empty label)';
  }

  function formatTime(ts) {
    const diff = Date.now() - ts * 1000;
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const d = new Date(ts * 1000);
    return d.toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
  }

  const ICON_FALLBACK_QR   = '/static/icons/solid/qrcode.svg';
  const ICON_FALLBACK_FONT = '/static/icons/solid/font.svg';

  function entryIconUrl(entry) {
    if (entry.icon) return iconPathToUrl(entry.icon);
    if (entry.url)  return ICON_FALLBACK_QR;
    return ICON_FALLBACK_FONT;
  }

  // Shared hover-preview card
  const hoverCard = document.createElement('div');
  hoverCard.className = 'history-hover-card';
  hoverCard.hidden = true;
  document.body.appendChild(hoverCard);
  let hoverHideTimer = null;

  function showHoverCard(anchorEl, previewUrl, createdAt) {
    clearTimeout(hoverHideTimer);
    const img = hoverCard.querySelector('img') || document.createElement('img');
    img.src = `${previewUrl}?ts=${Math.floor((createdAt || 0) * 1000)}`;
    img.alt = 'label preview';
    if (!hoverCard.contains(img)) hoverCard.appendChild(img);
    hoverCard.hidden = false;

    const rect = anchorEl.getBoundingClientRect();
    const cardW = 320;
    let left = rect.right + 12;
    if (left + cardW > window.innerWidth - 12) left = rect.left - cardW - 12;
    hoverCard.style.left = `${Math.max(8, left)}px`;
    hoverCard.style.top  = `${Math.max(8, rect.top + window.scrollY)}px`;
  }

  function hideHoverCard() {
    hoverHideTimer = setTimeout(() => { hoverCard.hidden = true; }, 120);
  }

  function buildHistoryRow(entry, showTime) {
    const row = document.createElement('div');
    row.className = 'history-entry';

    // Thumbnail — shows icon/QR/font indicator; hover reveals full label preview
    const thumb = document.createElement('div');
    thumb.className = 'history-entry__thumb';
    const iconUrl = entryIconUrl(entry);
    const iconImg = document.createElement('img');
    iconImg.src = iconUrl;
    iconImg.alt = '';
    iconImg.className = 'history-entry__thumb-icon';
    thumb.appendChild(iconImg);

    if (entry.preview_url) {
      thumb.style.cursor = 'pointer';
      thumb.addEventListener('mouseenter', () => showHoverCard(thumb, entry.preview_url, entry.created_at));
      thumb.addEventListener('mouseleave', hideHoverCard);
    }
    row.appendChild(thumb);

    // Meta
    const metaDiv = document.createElement('div');
    metaDiv.className = 'history-entry__meta';

    // Name — click to edit
    const nameEl = document.createElement('div');
    nameEl.className = 'history-entry__name history-entry__label';
    nameEl.title = 'Click to rename';
    nameEl.textContent = historyDisplayLabel(entry);
    nameEl.addEventListener('click', () => startRename(entry, nameEl));
    metaDiv.appendChild(nameEl);

    if (showTime && entry.created_at) {
      const timeEl = document.createElement('div');
      timeEl.className = 'history-entry__time';
      timeEl.textContent = formatTime(entry.created_at);
      metaDiv.appendChild(timeEl);
    }
    row.appendChild(metaDiv);

    // Action buttons
    const actions = document.createElement('div');
    actions.className = 'history-entry__actions';

    if (entry.starred) {
      // Library entry: Load, Reprint, Delete
      const loadBtn = document.createElement('button');
      loadBtn.type = 'button';
      loadBtn.className = 'secondary';
      loadBtn.textContent = 'Load';
      loadBtn.addEventListener('click', () => loadFromHistory(entry));
      actions.appendChild(loadBtn);

      const reprintBtn = document.createElement('button');
      reprintBtn.type = 'button';
      reprintBtn.className = 'secondary';
      reprintBtn.textContent = 'Reprint';
      reprintBtn.addEventListener('click', () => {
        reprintBtn.disabled = true;
        reprintBtn.textContent = 'Printing…';
        reprintFromHistory(entry).catch((err) => {
          reprintBtn.disabled = false;
          reprintBtn.textContent = 'Reprint';
          window.alert(`Reprint failed: ${err instanceof Error ? err.message : err}`);
        });
      });
      actions.appendChild(reprintBtn);

      const deleteBtn = document.createElement('button');
      deleteBtn.type = 'button';
      deleteBtn.className = 'secondary';
      deleteBtn.textContent = 'Delete';
      deleteBtn.addEventListener('click', () => {
        deleteBtn.disabled = true;
        fetch(`/api/history/${entry.id}`, { method: 'DELETE' })
          .then(() => renderHistory())
          .catch((err) => {
            deleteBtn.disabled = false;
            console.error('Delete failed', err);
          });
      });
      actions.appendChild(deleteBtn);
    } else {
      // Recent entry: Save, Load, Reprint
      const saveBtn = document.createElement('button');
      saveBtn.type = 'button';
      saveBtn.className = 'secondary';
      saveBtn.textContent = 'Save';
      saveBtn.addEventListener('click', () => {
        saveBtn.disabled = true;
        fetch(`/api/history/${entry.id}/star`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ starred: true, name: entry.name || null }),
        }).then(() => renderHistory()).catch((err) => {
          saveBtn.disabled = false;
          console.error('Save failed', err);
        });
      });
      actions.appendChild(saveBtn);

      const loadBtn = document.createElement('button');
      loadBtn.type = 'button';
      loadBtn.className = 'secondary';
      loadBtn.textContent = 'Load';
      loadBtn.addEventListener('click', () => loadFromHistory(entry));
      actions.appendChild(loadBtn);

      const reprintBtn = document.createElement('button');
      reprintBtn.type = 'button';
      reprintBtn.className = 'secondary';
      reprintBtn.textContent = 'Reprint';
      reprintBtn.addEventListener('click', () => {
        reprintBtn.disabled = true;
        reprintBtn.textContent = 'Printing…';
        reprintFromHistory(entry).catch((err) => {
          reprintBtn.disabled = false;
          reprintBtn.textContent = 'Reprint';
          window.alert(`Reprint failed: ${err instanceof Error ? err.message : err}`);
        });
      });
      actions.appendChild(reprintBtn);
    }

    row.appendChild(actions);
    return row;
  }

  function startRename(entry, nameEl) {
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'history-entry__name-input';
    input.value = entry.name || '';
    input.placeholder = historyDisplayLabel(entry);
    nameEl.replaceWith(input);
    input.focus();

    function commit() {
      const newName = input.value.trim() || null;
      entry.name = newName;
      fetch(`/api/history/${entry.id}/star`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ starred: entry.starred, name: newName }),
      }).then(() => renderHistory()).catch(console.error);
    }
    input.addEventListener('blur', commit);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
      if (e.key === 'Escape') { input.removeEventListener('blur', commit); renderHistory(); }
    });
  }

  function loadFromHistory(entry) {
    if (elements.labelText) elements.labelText.value = entry.text || '';
    if (elements.labelUrl) elements.labelUrl.value = entry.url || '';
    if (elements.fontSize) elements.fontSize.value = String(entry.font_size || 24);
    if (elements.fontSelect) elements.fontSelect.value = entry.font || defaultFontKey;
    if (elements.borderSelect) elements.borderSelect.value = entry.border_style || defaultBorderStyle;
    if (elements.labelWidth) elements.labelWidth.value = entry.label_width_mm != null ? String(entry.label_width_mm) : '';
    currentIconSize = entry.icon_size || iconMinHeight;
    currentQrSize = entry.qr_size || qrMinSize;
    if (elements.iconSizeInput) { elements.iconSizeInput.value = String(currentIconSize); updateIconSizeDisplay(); }
    if (elements.qrSizeInput) { elements.qrSizeInput.value = String(currentQrSize); updateQrSizeDisplay(); }
    updateIconUi(entry.icon || '', { url: entry.icon ? iconPathToUrl(entry.icon) : null });
    currentFileId = null;
    if (elements.printBtn) elements.printBtn.disabled = !printerAvailable || hasError;
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  async function renderHistory() {
    try {
      const res = await fetch('/api/history');
      if (!res.ok) return;
      const data = await res.json();

      const starred = data.starred || [];
      const recent = data.recent || [];

      if (elements.libraryCard) elements.libraryCard.hidden = starred.length === 0;
      if (elements.libraryList) {
        elements.libraryList.innerHTML = '';
        starred.forEach((entry) => elements.libraryList.appendChild(buildHistoryRow(entry, false)));
      }

      if (elements.historyCard) elements.historyCard.hidden = recent.length === 0;
      if (elements.historyList) {
        elements.historyList.innerHTML = '';
        recent.forEach((entry) => elements.historyList.appendChild(buildHistoryRow(entry, true)));
      }
    } catch (err) {
      console.error('Failed to load history', err);
    }
  }

  function currentPrintParams() {
    const labelWidthRaw = elements.labelWidth ? elements.labelWidth.value.trim() : '';
    return {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      timestamp: Date.now(),
      text: elements.labelText ? elements.labelText.value : '',
      url: elements.labelUrl ? elements.labelUrl.value : '',
      font_size: parseInt(elements.fontSize ? elements.fontSize.value : '24', 10) || 24,
      font: elements.fontSelect ? elements.fontSelect.value : defaultFontKey,
      border_style: elements.borderSelect ? elements.borderSelect.value : defaultBorderStyle,
      icon: elements.iconPath ? normalizeIconPath(elements.iconPath.value) : '',
      icon_size: currentIconSize,
      qr_size: currentQrSize,
      label_width_mm: labelWidthRaw !== '' ? parseFloat(labelWidthRaw) : null,
    };
  }

  function setPrintingState() {
    if (elements.statusBadge) {
      elements.statusBadge.className = 'badge printing';
      elements.statusBadge.textContent = 'Printing…';
    }
    if (elements.printBtn) elements.printBtn.disabled = true;
    setTimeout(() => fetchStatus().catch(console.error), 5000);
  }

  async function reprintFromHistory(entry) {
    if (!printerAvailable || hasError) throw new Error('Printer not ready');
    loadFromHistory(entry);

    // Regenerate preview then print
    await doPreview();
    if (!currentFileId) throw new Error('Preview generation failed');

    const res = await fetch('/api/print', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_id: currentFileId }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || data.stderr || `HTTP ${res.status}`);

    renderHistory();
    setPrintingState();
  }

  async function doPrint() {
    if (!printerAvailable || hasError) return;
    if (!currentFileId) {
      await doPreview();
      if (!currentFileId) return;
    }
    const res = await fetch('/api/print', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_id: currentFileId }),
    });
    const data = await res.json();
    if (res.ok && data.ok) {
      renderHistory();
      setPrintingState();
    } else {
      window.alert('Print failed: ' + (data.error || data.stderr || data.stdout || 'unknown error'));
    }
  }

  function parentDirectory(path) {
    const normalized = normalizeIconPath(path);
    if (!normalized) return '';
    const parts = normalized.split('/').filter(Boolean);
    parts.pop();
    return parts.join('/');
  }

  function switchTab(tab) {
    const isLocal = tab === 'local';
    if (elements.tabLocal) elements.tabLocal.classList.toggle('is-active', isLocal);
    if (elements.tabIconify) elements.tabIconify.classList.toggle('is-active', !isLocal);
    if (elements.panelLocal) elements.panelLocal.hidden = !isLocal;
    if (elements.panelIconify) elements.panelIconify.hidden = isLocal;
  }

  async function doLocalSearch(q) {
    const query = q.trim();
    const searching = query.length >= 2;
    if (elements.iconBrowsePanel) elements.iconBrowsePanel.hidden = searching;
    if (elements.iconSearchResults) {
      elements.iconSearchResults.hidden = !searching;
      elements.iconSearchResults.innerHTML = '';
    }
    if (elements.iconSearchEmpty) elements.iconSearchEmpty.hidden = true;
    if (!searching) return;

    if (elements.iconSearchEmpty) {
      elements.iconSearchEmpty.hidden = false;
      elements.iconSearchEmpty.textContent = 'Searching…';
    }

    try {
      const res = await fetch(`/api/icons/search?q=${encodeURIComponent(query)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      if (elements.iconSearchEmpty) elements.iconSearchEmpty.hidden = true;

      if (!data.icons || data.icons.length === 0) {
        if (elements.iconSearchEmpty) {
          elements.iconSearchEmpty.hidden = false;
          elements.iconSearchEmpty.textContent = `No icons found matching "${query}".`;
        }
        return;
      }

      if (elements.iconSearchResults) {
        data.icons.forEach((icon) => {
          const button = document.createElement('button');
          button.type = 'button';
          button.className = 'icon-tile';
          button.dataset.path = icon.path || '';
          button.dataset.url = icon.url || '';
          const img = document.createElement('img');
          img.src = icon.url || '';
          img.alt = icon.name || '';
          const label = document.createElement('span');
          label.textContent = icon.name || '';
          button.appendChild(img);
          button.appendChild(label);
          elements.iconSearchResults.appendChild(button);
        });
        highlightActiveIconTile();
      }
    } catch (err) {
      if (elements.iconSearchEmpty) {
        elements.iconSearchEmpty.hidden = false;
        elements.iconSearchEmpty.textContent = `Search failed: ${err instanceof Error ? err.message : err}`;
      }
    }
  }

  function buildIconTile(imgSrc, labelText, subLabelText, extraClass, dataAttrs) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `icon-tile${extraClass ? ' ' + extraClass : ''}`;
    Object.entries(dataAttrs || {}).forEach(([k, v]) => { button.dataset[k] = v; });
    const img = document.createElement('img');
    img.src = imgSrc || '';
    img.alt = labelText || '';
    const label = document.createElement('span');
    label.textContent = labelText || '';
    button.appendChild(img);
    button.appendChild(label);
    if (subLabelText) {
      const sub = document.createElement('span');
      sub.className = 'iconify-tile__set';
      sub.textContent = subLabelText;
      button.appendChild(sub);
    }
    return button;
  }

  async function doIconifySearch(q) {
    const query = q.trim();
    if (!query) return;
    if (elements.iconifyGrid) elements.iconifyGrid.innerHTML = '';
    if (elements.iconifyState) {
      elements.iconifyState.hidden = false;
      elements.iconifyState.textContent = 'Searching…';
    }
    try {
      const res = await fetch(`/api/iconify/search?q=${encodeURIComponent(query)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.error) throw new Error(data.error);

      if (!data.icons || data.icons.length === 0) {
        if (elements.iconifyState) {
          elements.iconifyState.hidden = false;
          elements.iconifyState.textContent = `No icons found for "${query}".`;
        }
        return;
      }

      if (elements.iconifyState) elements.iconifyState.hidden = true;

      if (elements.iconifyGrid) {
        data.icons.forEach((icon) => {
          const previewUrl = `https://api.iconify.design/${icon.prefix}/${icon.name}.svg?color=%23000000`;
          const tile = buildIconTile(previewUrl, icon.name, icon.prefix, 'iconify-tile', {
            prefix: icon.prefix,
            name: icon.name,
          });
          elements.iconifyGrid.appendChild(tile);
        });
      }
    } catch (err) {
      if (elements.iconifyState) {
        elements.iconifyState.hidden = false;
        elements.iconifyState.textContent = `Search failed: ${err instanceof Error ? err.message : err}. Check internet connection.`;
      }
    }
  }

  async function downloadAndSelectIconify(prefix, name, tileEl) {
    try {
      const res = await fetch('/api/iconify/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prefix, name }),
      });
      if (!res.ok) {
        let errMsg = `Server error ${res.status}`;
        try { const e = await res.json(); if (e.error) errMsg = e.error; } catch (_) {}
        throw new Error(errMsg);
      }
      const data = await res.json();
      updateIconUi(data.path, { url: data.url });
      closeIconModal();
    } catch (err) {
      if (tileEl) tileEl.disabled = false;
      window.alert(`Failed to download icon: ${err instanceof Error ? err.message : err}`);
    }
  }

  function openIconModal(startPath = '') {
    if (!elements.iconModal) return;
    iconModalIsOpen = true;
    document.body.classList.add('modal-open');
    elements.iconModal.classList.add('is-open');
    elements.iconModal.setAttribute('aria-hidden', 'false');
    switchTab('local');
    if (elements.iconSearch) elements.iconSearch.value = '';
    if (elements.iconSearchResults) { elements.iconSearchResults.innerHTML = ''; elements.iconSearchResults.hidden = true; }
    if (elements.iconSearchEmpty) elements.iconSearchEmpty.hidden = true;
    if (elements.iconBrowsePanel) elements.iconBrowsePanel.hidden = false;
    loadIconDirectory(normalizeIconPath(startPath));
  }

  function closeIconModal() {
    if (!elements.iconModal) return;
    iconModalIsOpen = false;
    document.body.classList.remove('modal-open');
    elements.iconModal.classList.remove('is-open');
    elements.iconModal.setAttribute('aria-hidden', 'true');
  }

  let diagnosticsModalIsOpen = false;

  function openDiagnosticsModal() {
    if (!elements.diagnosticsModal) return;
    diagnosticsModalIsOpen = true;
    document.body.classList.add('modal-open');
    elements.diagnosticsModal.classList.add('is-open');
    elements.diagnosticsModal.setAttribute('aria-hidden', 'false');
  }

  function closeDiagnosticsModal() {
    if (!elements.diagnosticsModal) return;
    diagnosticsModalIsOpen = false;
    document.body.classList.remove('modal-open');
    elements.diagnosticsModal.classList.remove('is-open');
    elements.diagnosticsModal.setAttribute('aria-hidden', 'true');
  }

  function renderBreadcrumbs(crumbs) {
    if (!elements.iconBreadcrumbs) return;
    elements.iconBreadcrumbs.innerHTML = '';
    crumbs.forEach((crumb, index) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = crumb.name || 'Icons';
      button.dataset.path = crumb.path || '';
      if (index === crumbs.length - 1) button.disabled = true;
      elements.iconBreadcrumbs.appendChild(button);
    });
  }

  function highlightActiveIconTile() {
    [elements.iconGrid, elements.iconSearchResults].forEach((grid) => {
      if (!grid) return;
      grid.querySelectorAll('button.icon-tile').forEach((tile) => {
        tile.classList.toggle('is-active', (tile.dataset.path || '') === currentIconPath);
      });
    });
  }

  function renderIconDirectory(data) {
    if (elements.iconDirList) {
      elements.iconDirList.innerHTML = '';
      data.dirs.forEach((dir) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'dir-tile';
        button.dataset.path = dir.path || '';
        button.textContent = dir.name;
        elements.iconDirList.appendChild(button);
      });
    }

    if (elements.iconGrid) {
      elements.iconGrid.innerHTML = '';
      data.icons.forEach((icon) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'icon-tile';
        button.dataset.path = icon.path || '';
        button.dataset.url = icon.url || '';

        const img = document.createElement('img');
        img.src = icon.url || '';
        img.alt = icon.name || '';

        const label = document.createElement('span');
        label.textContent = icon.name || '';

        button.appendChild(img);
        button.appendChild(label);
        elements.iconGrid.appendChild(button);
      });
    }

    const hasEntries = (data.dirs && data.dirs.length) || (data.icons && data.icons.length);
    if (elements.iconEmptyState) {
      if (hasEntries) {
        elements.iconEmptyState.hidden = true;
        elements.iconEmptyState.textContent = '';
      } else {
        elements.iconEmptyState.hidden = false;
        elements.iconEmptyState.textContent = 'Nothing to show here yet.';
      }
    }

    renderBreadcrumbs(data.breadcrumbs || []);
    highlightActiveIconTile();
  }

  async function loadIconDirectory(path = '') {
    if (!elements.iconDirList || !elements.iconGrid) return;
    elements.iconDirList.innerHTML = '';
    elements.iconGrid.innerHTML = '';
    if (elements.iconEmptyState) {
      elements.iconEmptyState.hidden = false;
      elements.iconEmptyState.textContent = 'Loading…';
    }

    try {
      const res = await fetch(`/api/icons?path=${encodeURIComponent(path)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      renderIconDirectory(data);
    } catch (err) {
      if (elements.iconEmptyState) {
        elements.iconEmptyState.hidden = false;
        elements.iconEmptyState.textContent = `Unable to load icons (${err instanceof Error ? err.message : err}).`;
      }
    }
  }

  function handleModalKeydown(event) {
    if (event.key === 'Escape') {
      if (iconModalIsOpen) {
        event.preventDefault();
        closeIconModal();
      } else if (diagnosticsModalIsOpen) {
        event.preventDefault();
        closeDiagnosticsModal();
      }
    }
  }

  if (elements.refreshBtn) {
    elements.refreshBtn.addEventListener('click', () => {
      fetchStatus().catch((err) => console.error('Failed to refresh status', err));
    });
  }

  if (elements.diagnosticsBtn) {
    elements.diagnosticsBtn.addEventListener('click', openDiagnosticsModal);
  }

  if (elements.diagnosticsModalClose) {
    elements.diagnosticsModalClose.addEventListener('click', closeDiagnosticsModal);
  }

  if (elements.diagnosticsModal) {
    const backdrop = elements.diagnosticsModal.querySelector('[data-close-diag]');
    if (backdrop) backdrop.addEventListener('click', closeDiagnosticsModal);
  }

  if (elements.previewBtn) {
    elements.previewBtn.addEventListener('click', () => {
      doPreview().catch((err) => {
        console.error('Preview failed', err);
        if (elements.previewPane) elements.previewPane.innerHTML = '<span class="muted">Preview failed. Check console for details.</span>';
      });
    });
  }

  if (elements.printBtn) {
    elements.printBtn.addEventListener('click', () => {
      doPrint().catch((err) => {
        console.error('Print failed', err);
        window.alert('Print failed due to an unexpected error. See console for details.');
      });
    });
  }

  if (elements.openIconPicker) {
    elements.openIconPicker.addEventListener('click', () => {
      const start = parentDirectory(currentIconPath);
      openIconModal(start);
    });
  }

  if (elements.clearIcon) {
    elements.clearIcon.addEventListener('click', () => {
      updateIconUi('', { url: null });
      iconSizeUserSet = false;
    });
  }

  if (elements.iconModalClose) {
    elements.iconModalClose.addEventListener('click', () => closeIconModal());
  }

  if (elements.iconModalBackdrop) {
    elements.iconModalBackdrop.addEventListener('click', () => closeIconModal());
  }

  if (elements.iconDirList) {
    elements.iconDirList.addEventListener('click', (event) => {
      const button = event.target.closest('button.dir-tile');
      if (!button) return;
      loadIconDirectory(button.dataset.path || '');
    });
  }

  if (elements.iconGrid) {
    elements.iconGrid.addEventListener('click', (event) => {
      const button = event.target.closest('button.icon-tile');
      if (!button) return;
      updateIconUi(button.dataset.path || '', { url: button.dataset.url || '' });
      closeIconModal();
    });
  }

  if (elements.iconBreadcrumbs) {
    elements.iconBreadcrumbs.addEventListener('click', (event) => {
      const button = event.target.closest('button');
      if (!button || button.disabled) return;
      loadIconDirectory(button.dataset.path || '');
    });
  }

  if (elements.iconSearchResults) {
    elements.iconSearchResults.addEventListener('click', (event) => {
      const button = event.target.closest('button.icon-tile');
      if (!button) return;
      updateIconUi(button.dataset.path || '', { url: button.dataset.url || '' });
      closeIconModal();
    });
  }

  if (elements.tabLocal) {
    elements.tabLocal.addEventListener('click', () => switchTab('local'));
  }

  if (elements.tabIconify) {
    elements.tabIconify.addEventListener('click', () => switchTab('iconify'));
  }

  if (elements.iconSearch) {
    elements.iconSearch.addEventListener('input', () => {
      clearTimeout(localSearchDebounce);
      localSearchDebounce = setTimeout(() => {
        doLocalSearch(elements.iconSearch.value).catch(console.error);
      }, 300);
    });
  }

  if (elements.iconifySearchBtn) {
    elements.iconifySearchBtn.addEventListener('click', () => {
      doIconifySearch(elements.iconifySearch ? elements.iconifySearch.value : '').catch(console.error);
    });
  }

  if (elements.iconifySearch) {
    elements.iconifySearch.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        doIconifySearch(elements.iconifySearch.value).catch(console.error);
      }
    });
  }

  if (elements.iconifyGrid) {
    elements.iconifyGrid.addEventListener('click', (event) => {
      const tile = event.target.closest('button.iconify-tile');
      if (!tile || tile.disabled) return;
      tile.disabled = true;
      const nameSpan = tile.querySelectorAll('span')[0];
      if (nameSpan) nameSpan.textContent = 'Downloading…';
      downloadAndSelectIconify(tile.dataset.prefix, tile.dataset.name, tile).catch(console.error);
    });
  }

  if (elements.clearHistoryBtn) {
    elements.clearHistoryBtn.addEventListener('click', async () => {
      try {
        const res = await fetch('/api/history');
        if (!res.ok) return;
        const data = await res.json();
        const recent = data.recent || [];
        await Promise.all(recent.map((entry) =>
          fetch(`/api/history/${entry.id}`, { method: 'DELETE' })
        ));
        renderHistory();
      } catch (err) {
        console.error('Failed to clear history', err);
      }
    });
  }

  document.addEventListener('keydown', handleModalKeydown);

  renderHistory();

  fetchStatus().catch((err) => {
    console.error('Initial status fetch failed', err);
    if (elements.previewPane) elements.previewPane.innerHTML = '<span class="muted">Unable to reach printer status service.</span>';
  });
})();
