(() => {
  'use strict';

  // --- Global app state (waypoints & pin always stored canonically in WGS-84) ---
  const state = {
    activeTab: 'run', // 'run' | 'pin'
    mapEditMode: 'draw', // 'draw' | 'edit'
    pickingTrackCenter: false,
    baseMapId: 'amap_sat_label',
    waypointsWgs: [], // [[lat, lng], ...]
    closedLoop: true,
    routeMode: 'loop', // 'loop' | 'shuttle' | 'open'
    pinWgs: null, // [lat, lng]
    trackCenterWgs: null, // [lat, lng]
    device: { connected: false, transport: '', device_info: {} },
    simulation: { mode: 'idle', trail: [] },
    savedRoutes: [],
    savedPins: [],
    logs: [],
    envInfo: null,
    guideStep: 0,
  };

  // --- Map & Layer definitions ---
  const BASE_LAYERS = {
    amap_sat_label: {
      crs: 'gcj02',
      build: () =>
        L.layerGroup([
          L.tileLayer('https://webst0{s}.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}', {
            subdomains: '1234',
            maxZoom: 20,
            maxNativeZoom: 18,
            attribution: '高德卫星图',
          }),
          L.tileLayer('https://webst0{s}.is.autonavi.com/appmaptile?style=8&x={x}&y={y}&z={z}', {
            subdomains: '1234',
            maxZoom: 20,
            maxNativeZoom: 18,
          }),
        ]),
    },
    amap_sat: {
      crs: 'gcj02',
      build: () =>
        L.tileLayer('https://webst0{s}.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}', {
          subdomains: '1234',
          maxZoom: 20,
          maxNativeZoom: 18,
          attribution: '高德纯卫星图',
        }),
    },
    amap_vec: {
      crs: 'gcj02',
      build: () =>
        L.tileLayer('https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}', {
          subdomains: '1234',
          maxZoom: 20,
          maxNativeZoom: 18,
          attribution: '高德标准地图',
        }),
    },
    esri_sat: {
      crs: 'wgs84',
      build: () =>
        L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
          maxZoom: 20,
          maxNativeZoom: 18,
          attribution: 'Esri World Imagery',
        }),
    },
    osm: {
      crs: 'wgs84',
      build: () =>
        L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
          maxZoom: 19,
          attribution: 'OpenStreetMap',
        }),
    },
  };

  function currentMapCrs() {
    return (BASE_LAYERS[state.baseMapId] || BASE_LAYERS.amap_sat_label).crs;
  }

  function wgsToMap(lat, lng) {
    return Coords.fromWgs84(lat, lng, currentMapCrs());
  }

  function mapToWgs(lat, lng) {
    return Coords.toWgs84(lat, lng, currentMapCrs());
  }

  // --- DOM helpers ---
  const $ = (id) => document.getElementById(id);

  function toast(msg, kind = 'info') {
    const box = document.createElement('div');
    box.className = `toast ${kind}`;
    box.textContent = msg;
    $('toastContainer').appendChild(box);
    setTimeout(() => {
      box.remove();
    }, 4200);
  }

  async function api(path, method = 'GET', body = null) {
    const opts = { method, headers: {} };
    if (body !== null) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const resp = await fetch(path, opts);
    const text = await resp.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      data = text;
    }
    if (!resp.ok) {
      const detail = data && data.detail ? data.detail : text || `HTTP ${resp.status}`;
      throw new Error(detail);
    }
    return data;
  }

  // --- Initialize Leaflet map ---
  const map = L.map('map', { zoomControl: true }).setView([39.9928, 116.3102], 16);
  let activeBaseLayer = null;

  const routeLine = L.polyline([], { color: '#2563eb', weight: 4, opacity: 0.9 }).addTo(map);
  const trailLine = L.polyline([], { color: '#22c55e', weight: 4, opacity: 0.85, dashArray: '6, 4' }).addTo(map);
  const waypointLayer = L.layerGroup().addTo(map);
  let pinMarker = null;
  let liveMarker = null;
  let trackCenterMarker = null;

  function setBaseMap(layerId) {
    const prevCrs = currentMapCrs();
    const center = map.getCenter();
    const centerWgs = Coords.toWgs84(center.lat, center.lng, prevCrs);

    if (activeBaseLayer) map.removeLayer(activeBaseLayer);
    state.baseMapId = layerId;
    activeBaseLayer = BASE_LAYERS[layerId].build().addTo(map);

    const newCenter = wgsToMap(centerWgs[0], centerWgs[1]);
    map.setView(newCenter, map.getZoom(), { animate: false });
    renderAllMapOverlays();
  }

  // --- Map rendering ---
  function renderRouteOverlay() {
    waypointLayer.clearLayers();
    const mapPts = state.waypointsWgs.map(([lat, lng]) => wgsToMap(lat, lng));
    const polyPts = [...mapPts];
    if (state.routeMode === 'loop' && mapPts.length >= 2) {
      polyPts.push(mapPts[0]);
    }
    routeLine.setLatLngs(polyPts);

    mapPts.forEach((mPt, idx) => {
      const cls = idx === 0 ? 'start' : idx === mapPts.length - 1 ? 'end' : '';
      const icon = L.divIcon({
        className: '',
        html: `<div class="wp-dot ${cls}">${idx + 1}</div>`,
        iconSize: [22, 22],
        iconAnchor: [11, 11],
      });
      const marker = L.marker(mPt, {
        icon,
        draggable: state.mapEditMode === 'edit',
      });
      marker.on('dragend', (ev) => {
        const ll = ev.target.getLatLng();
        const [wLat, wLng] = mapToWgs(ll.lat, ll.lng);
        state.waypointsWgs[idx] = [Number(wLat.toFixed(7)), Number(wLng.toFixed(7))];
        renderRouteOverlay();
        renderWaypointTable();
      });
      marker.on('contextmenu', () => {
        // Right-click a waypoint to delete it
        state.waypointsWgs.splice(idx, 1);
        renderRouteOverlay();
        renderWaypointTable();
      });
      marker.addTo(waypointLayer);
    });

    const isLoop = state.routeMode === 'loop';
    let len = Coords.routeLengthMeters(state.waypointsWgs, isLoop);
    let lenLabel = `${len.toFixed(1)} m`;
    if (state.routeMode === 'shuttle' && state.waypointsWgs.length >= 2) {
      lenLabel = `单程 ${len.toFixed(1)} m (往返 ${(len * 2).toFixed(1)} m)`;
    }
    $('routeSummary').textContent = `${state.waypointsWgs.length} 点 · ${lenLabel}`;
  }

  function renderPinOverlay() {
    if (!state.pinWgs) {
      if (pinMarker) {
        map.removeLayer(pinMarker);
        pinMarker = null;
      }
      return;
    }
    const [mLat, mLng] = wgsToMap(state.pinWgs[0], state.pinWgs[1]);
    if (!pinMarker) {
      pinMarker = L.marker([mLat, mLng], { draggable: true }).addTo(map);
      pinMarker.on('dragend', (ev) => {
        const ll = ev.target.getLatLng();
        const [wLat, wLng] = mapToWgs(ll.lat, ll.lng);
        setPinFromWgs(wLat, wLng, false);
      });
    } else {
      pinMarker.setLatLng([mLat, mLng]);
    }
  }

  function renderSimulationOverlay() {
    const sim = state.simulation || {};
    const trail = Array.isArray(sim.trail) ? sim.trail : [];
    trailLine.setLatLngs(trail.map(([lat, lng]) => wgsToMap(lat, lng)));

    if (sim.current_lat != null && sim.current_lng != null) {
      const [mLat, mLng] = wgsToMap(sim.current_lat, sim.current_lng);
      if (!liveMarker) {
        const icon = L.divIcon({
          className: '',
          html: '<div class="live-gps-dot"></div>',
          iconSize: [18, 18],
          iconAnchor: [9, 9],
        });
        liveMarker = L.marker([mLat, mLng], { icon, interactive: false, zIndexOffset: 1000 }).addTo(map);
      } else {
        liveMarker.setLatLng([mLat, mLng]);
      }
    } else if (liveMarker) {
      map.removeLayer(liveMarker);
      liveMarker = null;
    }
  }

  function renderTrackCenterMarker() {
    if (!state.trackCenterWgs) {
      if (trackCenterMarker) {
        map.removeLayer(trackCenterMarker);
        trackCenterMarker = null;
      }
      return;
    }
    const [mLat, mLng] = wgsToMap(state.trackCenterWgs[0], state.trackCenterWgs[1]);
    if (!trackCenterMarker) {
      trackCenterMarker = L.circleMarker([mLat, mLng], {
        radius: 6,
        color: '#f59e0b',
        fillColor: '#fef3c7',
        fillOpacity: 0.9,
      }).addTo(map);
    } else {
      trackCenterMarker.setLatLng([mLat, mLng]);
    }
  }

  function renderAllMapOverlays() {
    renderRouteOverlay();
    renderPinOverlay();
    renderSimulationOverlay();
    renderTrackCenterMarker();
  }

  // --- Waypoint Table ---
  function renderWaypointTable() {
    const tbody = $('wpTableBody');
    const crs = $('selTableCrs').value;
    tbody.innerHTML = '';
    state.waypointsWgs.forEach(([wLat, wLng], idx) => {
      const [dLat, dLng] = Coords.fromWgs84(wLat, wLng, crs);
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${idx + 1}</td>
        <td><input type="number" step="0.000001" value="${dLat.toFixed(7)}" data-idx="${idx}" data-axis="lat" /></td>
        <td><input type="number" step="0.000001" value="${dLng.toFixed(7)}" data-idx="${idx}" data-axis="lng" /></td>
        <td><button class="btn btn-sm" data-del="${idx}">删</button></td>
      `;
      tbody.appendChild(tr);
    });
  }

  $('wpTableBody').addEventListener('change', (ev) => {
    const inp = ev.target;
    if (!inp || !inp.dataset || inp.dataset.idx === undefined) return;
    const idx = Number(inp.dataset.idx);
    const val = parseFloat(inp.value);
    if (!Number.isFinite(val)) return;
    const crs = $('selTableCrs').value;
    const [curLat, curLng] = Coords.fromWgs84(state.waypointsWgs[idx][0], state.waypointsWgs[idx][1], crs);
    const nextLat = inp.dataset.axis === 'lat' ? val : curLat;
    const nextLng = inp.dataset.axis === 'lng' ? val : curLng;
    const [wLat, wLng] = Coords.toWgs84(nextLat, nextLng, crs);
    state.waypointsWgs[idx] = [Number(wLat.toFixed(7)), Number(wLng.toFixed(7))];
    renderRouteOverlay();
  });

  $('wpTableBody').addEventListener('click', (ev) => {
    const btn = ev.target.closest('button[data-del]');
    if (!btn) return;
    const idx = Number(btn.dataset.del);
    state.waypointsWgs.splice(idx, 1);
    renderRouteOverlay();
    renderWaypointTable();
  });

  $('selTableCrs').addEventListener('change', renderWaypointTable);

  $('btnAddRow').addEventListener('click', () => {
    let newWgs;
    if (state.waypointsWgs.length > 0) {
      const last = state.waypointsWgs[state.waypointsWgs.length - 1];
      newWgs = [Number((last[0] + 0.0001).toFixed(7)), Number(last[1].toFixed(7))];
    } else {
      const c = map.getCenter();
      newWgs = mapToWgs(c.lat, c.lng);
      newWgs = [Number(newWgs[0].toFixed(7)), Number(newWgs[1].toFixed(7))];
    }
    state.waypointsWgs.push(newWgs);
    renderRouteOverlay();
    renderWaypointTable();
  });

  // --- Pin helpers ---
  function updatePinInputsFromWgs(wLat, wLng) {
    const crs = $('selPinCrs').value;
    const [dLat, dLng] = Coords.fromWgs84(wLat, wLng, crs);
    $('inpPinLat').value = dLat.toFixed(7);
    $('inpPinLng').value = dLng.toFixed(7);
    const [gLat, gLng] = Coords.fromWgs84(wLat, wLng, 'gcj02');
    $('pinCoordPreview').textContent = `WGS-84: ${wLat.toFixed(6)}, ${wLng.toFixed(6)} | GCJ-02: ${gLat.toFixed(6)}, ${gLng.toFixed(6)}`;
  }

  function setPinFromWgs(wLat, wLng, panMap = false) {
    state.pinWgs = [Number(wLat.toFixed(7)), Number(wLng.toFixed(7))];
    updatePinInputsFromWgs(state.pinWgs[0], state.pinWgs[1]);
    renderPinOverlay();
    if (panMap) {
      map.panTo(wgsToMap(state.pinWgs[0], state.pinWgs[1]));
    }
  }

  $('selPinCrs').addEventListener('change', () => {
    if (state.pinWgs) {
      updatePinInputsFromWgs(state.pinWgs[0], state.pinWgs[1]);
    }
  });

  const handleManualPinInput = () => {
    const lat = parseFloat($('inpPinLat').value);
    const lng = parseFloat($('inpPinLng').value);
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) return;
    if (lat < -90 || lat > 90 || lng < -180 || lng > 180) return;
    const [wLat, wLng] = Coords.toWgs84(lat, lng, $('selPinCrs').value);
    state.pinWgs = [Number(wLat.toFixed(7)), Number(wLng.toFixed(7))];
    const [gLat, gLng] = Coords.fromWgs84(wLat, wLng, 'gcj02');
    $('pinCoordPreview').textContent = `WGS-84: ${wLat.toFixed(6)}, ${wLng.toFixed(6)} | GCJ-02: ${gLat.toFixed(6)}, ${gLng.toFixed(6)}`;
    renderPinOverlay();
  };

  $('inpPinLat').addEventListener('input', handleManualPinInput);
  $('inpPinLng').addEventListener('input', handleManualPinInput);

  // --- Map Click handling ---
  map.on('click', (ev) => {
    hideSearchResults();
    const [wLat, wLng] = mapToWgs(ev.latlng.lat, ev.latlng.lng);
    if (state.pickingTrackCenter) {
      state.pickingTrackCenter = false;
      $('btnPickTrackCenter').textContent = '🎯 在地图点选操场中心';
      state.trackCenterWgs = [Number(wLat.toFixed(7)), Number(wLng.toFixed(7))];
      renderTrackCenterMarker();
      generateTrackFromControls();
      return;
    }

    if (state.activeTab === 'run') {
      if (state.mapEditMode === 'draw') {
        state.waypointsWgs.push([Number(wLat.toFixed(7)), Number(wLng.toFixed(7))]);
        renderRouteOverlay();
        renderWaypointTable();
      }
    } else {
      setPinFromWgs(wLat, wLng, false);
    }
  });

  // --- Tabs & Subtabs ---
  document.querySelectorAll('.tab-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab-btn').forEach((b) => b.classList.remove('active'));
      document.querySelectorAll('.tab-pane').forEach((p) => p.classList.remove('active'));
      btn.classList.add('active');
      const tab = btn.dataset.tab;
      state.activeTab = tab;
      $(`tab-${tab}`).classList.add('active');
    });
  });

  document.querySelectorAll('.subtab-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.subtab-btn').forEach((b) => b.classList.remove('active'));
      document.querySelectorAll('.subpane').forEach((p) => p.classList.remove('active'));
      btn.classList.add('active');
      $(`subtab-${btn.dataset.subtab}`).classList.add('active');
    });
  });

  // --- Route draw / edit / tools ---
  function setEditMode(mode) {
    state.mapEditMode = mode;
    $('btnModeDraw').classList.toggle('active', mode === 'draw');
    $('btnModeEdit').classList.toggle('active', mode === 'edit');
    renderRouteOverlay();
  }

  function setRouteMode(mode) {
    state.routeMode = mode;
    state.closedLoop = mode === 'loop';
    if ($('selRouteMode').value !== mode) $('selRouteMode').value = mode;
    $('rowShuttleOffset').style.display = mode === 'shuttle' ? 'flex' : 'none';
    renderRouteOverlay();
  }

  $('btnModeDraw').addEventListener('click', () => setEditMode('draw'));
  $('btnModeEdit').addEventListener('click', () => {
    setEditMode('edit');
    toast('已进入拖拽模式，拖动节点可微调位置（右键节点可删除）');
  });

  $('selRouteMode').addEventListener('change', (ev) => {
    setRouteMode(ev.target.value);
  });

  $('btnSixPointTrack').addEventListener('click', async () => {
    try {
      let cWgs = state.trackCenterWgs;
      if (!cWgs) {
        const c = map.getCenter();
        cWgs = mapToWgs(c.lat, c.lng);
      }
      const res = await api('/api/route/generate-six-point', 'POST', {
        center_lat: cWgs[0],
        center_lng: cWgs[1],
        center_crs: 'wgs84',
        length_m: 120.0,
        width_m: 60.0,
        rotation_deg: 0.0,
        counterclockwise: true,
      });
      state.waypointsWgs = res.points;
      setRouteMode('loop');
      setEditMode('edit');
      renderWaypointTable();
      toast(`已生成 6 点自由跑道（约 ${res.length_closed_m} m），请直接拖动 6 个节点对齐场地，也可点“弯道平滑”`, 'success');
    } catch (err) {
      toast(err.message, 'error');
    }
  });

  $('btnUndoPoint').addEventListener('click', () => {
    state.waypointsWgs.pop();
    renderRouteOverlay();
    renderWaypointTable();
  });

  $('btnClearRoute').addEventListener('click', () => {
    state.waypointsWgs = [];
    renderRouteOverlay();
    renderWaypointTable();
  });

  $('btnReverseRoute').addEventListener('click', () => {
    state.waypointsWgs.reverse();
    renderRouteOverlay();
    renderWaypointTable();
  });

  $('btnSmoothRoute').addEventListener('click', async () => {
    if (state.waypointsWgs.length < 3) {
      toast('至少需要 3 个节点才能平滑弯道', 'warn');
      return;
    }
    try {
      const res = await api('/api/route/smooth', 'POST', {
        points: state.waypointsWgs,
        closed: state.routeMode === 'loop',
        samples_per_segment: 6,
      });
      state.waypointsWgs = res.points;
      renderRouteOverlay();
      renderWaypointTable();
      toast(`弯道已平滑，共 ${res.points.length} 点 (${res.length_m} m)`, 'success');
    } catch (err) {
      toast(err.message, 'error');
    }
  });

  // --- Track Generator ---
  async function generateTrackFromControls() {
    if (!state.trackCenterWgs) {
      const c = map.getCenter();
      const [wLat, wLng] = mapToWgs(c.lat, c.lng);
      state.trackCenterWgs = [Number(wLat.toFixed(7)), Number(wLng.toFixed(7))];
      renderTrackCenterMarker();
    }
    const straight = parseFloat($('rngStraight').value);
    const radius = parseFloat($('rngRadius').value);
    const rotation = parseFloat($('rngRotation').value);
    const ptsCount = parseInt($('inpTrackPts').value, 10) || 60;
    const ccw = $('selTrackDir').value === 'ccw';

    $('lblStraight').textContent = straight.toFixed(1);
    $('lblRadius').textContent = radius.toFixed(1);
    $('lblRotation').textContent = rotation.toFixed(0);
    const estPerim = 2 * straight + 2 * Math.PI * radius;
    $('lblGenPerimeter').textContent = `(当前约 ${estPerim.toFixed(0)} m)`;

    try {
      const res = await api('/api/route/generate-track', 'POST', {
        center_lat: state.trackCenterWgs[0],
        center_lng: state.trackCenterWgs[1],
        center_crs: 'wgs84',
        straight_m: straight,
        radius_m: radius,
        rotation_deg: rotation,
        points_per_lap: ptsCount,
        counterclockwise: ccw,
      });
      state.waypointsWgs = res.points;
      setRouteMode('loop');
      renderWaypointTable();
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  $('btnPickTrackCenter').addEventListener('click', () => {
    state.pickingTrackCenter = true;
    $('btnPickTrackCenter').textContent = '👉 请点击地图操场中心...';
    toast('请在右侧卫星图操场中央点击一下');
  });

  $('btnGenerateTrackNow').addEventListener('click', generateTrackFromControls);
  ['rngStraight', 'rngRadius', 'rngRotation'].forEach((id) => {
    $(id).addEventListener('input', generateTrackFromControls);
  });
  ['inpTrackPts', 'selTrackDir'].forEach((id) => {
    $(id).addEventListener('change', generateTrackFromControls);
  });

  // --- Import / Export ---
  $('fileRouteImport').addEventListener('change', async (ev) => {
    const file = ev.target.files[0];
    if (!file) return;
    $('txtImportRoute').value = await file.text();
    if (file.name.toLowerCase().endsWith('.gpx')) {
      $('selImportCrs').value = 'wgs84';
    }
  });

  $('btnParseImport').addEventListener('click', async () => {
    const text = $('txtImportRoute').value.trim();
    if (!text) {
      toast('请先粘贴坐标或选择文件', 'warn');
      return;
    }
    try {
      const res = await api('/api/route/parse', 'POST', {
        text,
        input_crs: $('selImportCrs').value,
        lng_first: $('chkLngFirst').checked,
      });
      state.waypointsWgs = res.points;
      renderRouteOverlay();
      renderWaypointTable();
      if (res.points.length > 0) {
        const bounds = L.latLngBounds(res.points.map(([lat, lng]) => wgsToMap(lat, lng)));
        map.fitBounds(bounds, { padding: [40, 40], maxZoom: 18 });
      }
      toast(`导入成功：${res.count} 个路径点`, 'success');
    } catch (err) {
      toast(err.message, 'error');
    }
  });

  async function copyExport(outputCrs) {
    if (state.waypointsWgs.length === 0) {
      toast('当前没有可导出的路线点', 'warn');
      return;
    }
    const resp = await fetch('/api/route/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        points: state.waypointsWgs,
        format: 'json',
        output_crs: outputCrs,
      }),
    });
    const text = await resp.text();
    $('txtImportRoute').value = text;
    try {
      await navigator.clipboard.writeText(text);
      toast(`已导出并复制到剪贴板 (${outputCrs.toUpperCase()})`, 'success');
    } catch {
      toast(`已生成 ${outputCrs.toUpperCase()} 文本到输入框，请手动复制`, 'info');
    }
  }

  $('btnExportJsonBd09').addEventListener('click', () => copyExport('bd09'));
  $('btnExportJsonWgs84').addEventListener('click', () => copyExport('wgs84'));

  $('btnExportGpx').addEventListener('click', async () => {
    if (state.waypointsWgs.length === 0) {
      toast('当前没有可导出的路线点', 'warn');
      return;
    }
    const resp = await fetch('/api/route/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        points: state.waypointsWgs,
        format: 'gpx',
        speed_mps: parseFloat($('inpSpeedMps').value) || 3.0,
      }),
    });
    const text = await resp.text();
    const blob = new Blob([text], { type: 'application/gpx+xml' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'campus_run.gpx';
    a.click();
    URL.revokeObjectURL(url);
  });

  // --- Saved Routes & Pins ---
  async function loadSavedLists() {
    try {
      state.savedRoutes = await api('/api/routes');
      state.savedPins = await api('/api/pins');
      renderSavedRoutes();
      renderSavedPins();
    } catch {
      // ignore initial error
    }
  }

  function renderSavedRoutes() {
    const el = $('savedRoutesList');
    if (!state.savedRoutes.length) {
      el.innerHTML = '<div class="hint-box">暂无收藏路线，在地图画好跑道后可在此保存。</div>';
      return;
    }
    el.innerHTML = '';
    state.savedRoutes.forEach((r) => {
      const div = document.createElement('div');
      div.className = 'saved-item';
      div.innerHTML = `
        <div class="saved-item-info">
          <div class="saved-item-name"></div>
          <div class="saved-item-meta">${(r.points || []).length} 点 · ${r.updated_at || ''}</div>
        </div>
        <div style="display:flex;gap:4px;">
          <button class="btn-primary btn-sm" data-load-route="${r.id}">加载</button>
          <button class="btn btn-sm" data-del-route="${r.id}">删</button>
        </div>
      `;
      div.querySelector('.saved-item-name').textContent = r.name;
      el.appendChild(div);
    });
  }

  $('savedRoutesList').addEventListener('click', async (ev) => {
    const loadBtn = ev.target.closest('button[data-load-route]');
    const delBtn = ev.target.closest('button[data-del-route]');
    if (loadBtn) {
      const r = state.savedRoutes.find((x) => x.id === loadBtn.dataset.loadRoute);
      if (!r) return;
      state.waypointsWgs = r.points || [];
      setRouteMode(r.closed === false ? 'open' : 'loop');
      renderWaypointTable();
      if (state.waypointsWgs.length > 0) {
        const bounds = L.latLngBounds(state.waypointsWgs.map(([lat, lng]) => wgsToMap(lat, lng)));
        map.fitBounds(bounds, { padding: [40, 40], maxZoom: 18 });
      }
      toast(`已加载路线：${r.name}`, 'success');
    } else if (delBtn) {
      await api(`/api/routes/${delBtn.dataset.delRoute}`, 'DELETE');
      await loadSavedLists();
    }
  });

  $('btnSaveCurrentRoute').addEventListener('click', async () => {
    if (state.waypointsWgs.length < 2) {
      toast('请先设计至少 2 个路线点再保存', 'warn');
      return;
    }
    const name = $('inpSaveRouteName').value.trim() || `校园跑道 ${new Date().toLocaleTimeString()}`;
    await api('/api/routes', 'POST', {
      name,
      points: state.waypointsWgs,
      closed: state.closedLoop,
    });
    $('inpSaveRouteName').value = '';
    await loadSavedLists();
    toast('路线已收藏', 'success');
  });

  function renderSavedPins() {
    const el = $('savedPinsList');
    if (!state.savedPins.length) {
      el.innerHTML = '<div class="hint-box">暂无常用打卡点，选点后点击【收藏当前点】。</div>';
      return;
    }
    el.innerHTML = '';
    state.savedPins.forEach((p) => {
      const div = document.createElement('div');
      div.className = 'saved-item';
      div.innerHTML = `
        <div class="saved-item-info">
          <div class="saved-item-name"></div>
          <div class="saved-item-meta">${Number(p.lat).toFixed(5)}, ${Number(p.lng).toFixed(5)}</div>
        </div>
        <div style="display:flex;gap:4px;">
          <button class="btn-primary btn-sm" data-use-pin="${p.id}">定位至此</button>
          <button class="btn btn-sm" data-del-pin="${p.id}">删</button>
        </div>
      `;
      div.querySelector('.saved-item-name').textContent = p.name;
      el.appendChild(div);
    });
  }

  $('savedPinsList').addEventListener('click', async (ev) => {
    const useBtn = ev.target.closest('button[data-use-pin]');
    const delBtn = ev.target.closest('button[data-del-pin]');
    if (useBtn) {
      const p = state.savedPins.find((x) => x.id === useBtn.dataset.usePin);
      if (!p) return;
      setPinFromWgs(p.lat, p.lng, true);
      if (state.device.connected) {
        try {
          const res = await api('/api/pin/set', 'POST', {
            lat: p.lat,
            lng: p.lng,
            crs: 'wgs84',
            micro_drift_m: parseFloat($('inpPinDrift').value) || 0,
          });
          applySimulationState(res.simulation);
          toast(`已锁定到：${p.name}`, 'success');
        } catch (err) {
          toast(err.message, 'error');
        }
      } else {
        toast(`已选中打卡点：${p.name}（连接 iPhone 后点击锁定）`);
      }
    } else if (delBtn) {
      await api(`/api/pins/${delBtn.dataset.delPin}`, 'DELETE');
      await loadSavedLists();
    }
  });

  $('btnSavePin').addEventListener('click', async () => {
    if (!state.pinWgs) {
      toast('请先在地图点选或输入打卡经纬度', 'warn');
      return;
    }
    const name = $('inpSavePinName').value.trim() || `打卡点 ${new Date().toLocaleTimeString()}`;
    await api('/api/pins', 'POST', {
      name,
      lat: state.pinWgs[0],
      lng: state.pinWgs[1],
      crs: 'wgs84',
    });
    $('inpSavePinName').value = '';
    await loadSavedLists();
    toast('打卡点已收藏', 'success');
  });

  // --- Speed <-> Pace two-way binding ---
  function formatPace(secPerKm) {
    if (!Number.isFinite(secPerKm) || secPerKm <= 0) return '-';
    const total = Math.round(secPerKm);
    const min = Math.floor(total / 60);
    const sec = total % 60;
    return `${min}'${String(sec).padStart(2, '0')}"`;
  }

  function updatePacePreview() {
    const base = parseFloat($('inpSpeedMps').value) || 3.2;
    const prof = $('selPaceProfile').value;
    const fatPct = Math.max(0, Math.min(40, parseFloat($('inpFatiguePct').value) || 0));
    const f = fatPct / 100;
    const showFatigue = prof !== 'steady';
    $('rowFatiguePct').style.display = showFatigue ? 'flex' : 'none';
    $('rowEndSprint').style.display = prof === 'fatigue' ? 'flex' : 'none';

    if (prof === 'steady' || f <= 0) {
      $('pacePreviewChip').textContent = `预计配速：全程匀速约 ${formatPace(1000 / base)}/km (${base.toFixed(1)} m/s)`;
    } else if (prof === 'negative_split') {
      const s1 = Math.max(0.2, base * (1 - 0.45 * f));
      const s2 = base * (1 + 0.45 * f);
      $('pacePreviewChip').textContent = `预计配速：第1圈 ${formatPace(1000 / s1)}/km → 末圈 ${formatPace(1000 / s2)}/km`;
    } else {
      const s1 = base * (1 + 0.55 * f);
      const s2 = Math.max(0.2, base * Math.max(0.55, 1 - 0.60 * f));
      $('pacePreviewChip').textContent = `预计配速：第1圈 ${formatPace(1000 / s1)}/km → 末圈 ${formatPace(1000 / s2)}/km`;
    }
  }

  $('inpSpeedMps').addEventListener('input', () => {
    const spd = parseFloat($('inpSpeedMps').value);
    if (Number.isFinite(spd) && spd > 0.1) {
      $('inpPaceStr').value = formatPace(1000 / spd);
    }
    updatePacePreview();
  });

  $('inpSpeedMps').addEventListener('change', async () => {
    const spd = parseFloat($('inpSpeedMps').value);
    updatePacePreview();
    if (state.simulation.mode === 'running' || state.simulation.mode === 'paused') {
      if (Number.isFinite(spd) && spd > 0.1) {
        try {
          await api('/api/run/params', 'POST', { speed_mps: spd });
          toast(`跑步速度已实时调整为 ${spd.toFixed(1)} m/s`, 'info');
        } catch (err) {
          toast(err.message, 'error');
        }
      }
    }
  });

  $('inpPaceStr').addEventListener('change', async () => {
    const raw = $('inpPaceStr').value.trim();
    const m = raw.match(/(\d+)\D+(\d+)/);
    if (!m) return;
    const totalSec = parseInt(m[1], 10) * 60 + parseInt(m[2], 10);
    if (totalSec <= 0) return;
    const spd = Number((1000 / totalSec).toFixed(2));
    $('inpSpeedMps').value = spd;
    updatePacePreview();
    if (state.simulation.mode === 'running' || state.simulation.mode === 'paused') {
      try {
        await api('/api/run/params', 'POST', { speed_mps: spd });
        toast(`配速已实时调整为 ${raw} (${spd} m/s)`, 'info');
      } catch (err) {
        toast(err.message, 'error');
      }
    }
  });

  ['inpSpeedJitter', 'inpGpsDrift', 'inpInterval', 'selPaceProfile', 'inpFatiguePct', 'chkEndSprint'].forEach((id) => {
    $(id).addEventListener('change', async () => {
      updatePacePreview();
      if (state.simulation.mode === 'running' || state.simulation.mode === 'paused') {
        try {
          await api('/api/run/params', 'POST', {
            speed_jitter_pct: parseFloat($('inpSpeedJitter').value) || 0,
            gps_drift_m: parseFloat($('inpGpsDrift').value) || 0,
            interval_s: parseFloat($('inpInterval').value) || 1.0,
            pace_profile: $('selPaceProfile').value,
            fatigue_pct: parseFloat($('inpFatiguePct').value) || 0,
            end_sprint: $('chkEndSprint').checked,
          });
        } catch {
          // ignore
        }
      }
    });
  });
  $('inpFatiguePct').addEventListener('input', updatePacePreview);

  $('selLimitMode').addEventListener('change', (ev) => {
    const isLaps = ev.target.value === 'laps';
    $('rowLaps').style.display = isLaps ? 'flex' : 'none';
    $('rowDistance').style.display = isLaps ? 'none' : 'flex';
  });

  // --- Device Controls ---
  async function refreshDevices(silent = false) {
    try {
      const data = await api('/api/devices');
      const sel = $('deviceSelect');
      const prev = sel.value;
      sel.innerHTML = '';

      (data.usb || []).forEach((d) => {
        const opt = document.createElement('option');
        opt.value = `usb|${d.udid}`;
        const devModeTag = d.developer_mode === false ? ' [需开开发者模式]' : '';
        const verTag = d.ios_version ? `iOS ${d.ios_version}` : '未解锁/未信任';
        opt.textContent = `[USB] ${d.name} (${verTag})${devModeTag}`;
        sel.appendChild(opt);
      });

      (data.wifi || []).forEach((w) => {
        const opt = document.createElement('option');
        opt.value = `wifi|${w.id}`;
        opt.textContent = `[Wi-Fi] 已配对设备 (${w.id.slice(0, 12)}...)`;
        sel.appendChild(opt);
      });

      if (sel.options.length === 0) {
        const opt = document.createElement('option');
        opt.value = '';
        opt.textContent = '未检测到 iPhone（请插 USB 并点信任电脑）';
        sel.appendChild(opt);
      } else if (prev && [...sel.options].some((o) => o.value === prev)) {
        sel.value = prev;
      }
      if (!silent) toast(`扫描完成：发现 ${(data.usb || []).length} 台 USB 设备`);
    } catch (err) {
      if (!silent) toast(err.message, 'error');
    }
  }

  function selectedDeviceParams() {
    const raw = $('deviceSelect').value;
    if (!raw || !raw.includes('|')) return { transport: 'usb', udid: null };
    const [transport, udid] = raw.split('|');
    return { transport, udid };
  }

  function applyDeviceState(dev) {
    state.device = dev || { connected: false, device_info: {} };
    const pill = $('deviceStatusPill');
    const txt = $('deviceStatusText');
    if (state.device.connected) {
      pill.classList.add('connected');
      const info = state.device.device_info || {};
      const via = state.device.transport === 'wifi' ? 'Wi-Fi' : 'USB';
      txt.textContent = `${info.name || 'iPhone'} · iOS ${info.ios_version || '?'} (${via})`;
      $('btnConnect').style.display = 'none';
      $('btnDisconnect').style.display = 'inline-block';
      $('btnMountDdi').style.display = state.device.ddi_mounted ? 'none' : 'inline-block';
    } else {
      pill.classList.remove('connected');
      txt.textContent = state.device.status_message || '未连接设备';
      $('btnConnect').style.display = 'inline-block';
      $('btnDisconnect').style.display = 'none';
      $('btnMountDdi').style.display = 'none';
    }
  }

  $('btnRefreshDevices').addEventListener('click', () => refreshDevices(false));

  $('btnConnect').addEventListener('click', async () => {
    const { transport, udid } = selectedDeviceParams();
    $('btnConnect').disabled = true;
    $('btnConnect').textContent = '正在连接...';
    try {
      const res = await api('/api/device/connect', 'POST', {
        udid,
        transport,
        auto_mount_ddi: true,
      });
      applyDeviceState(res);
      toast(res.status_message || '设备连接成功', 'success');
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      $('btnConnect').disabled = false;
      $('btnConnect').textContent = '连接设备';
    }
  });

  $('btnDisconnect').addEventListener('click', async () => {
    try {
      const res = await api('/api/device/disconnect', 'POST', { clear_location: false });
      applyDeviceState(res);
      toast('已断开设备连接');
    } catch (err) {
      toast(err.message, 'error');
    }
  });

  $('btnResetLocation').addEventListener('click', async () => {
    try {
      const res = await api('/api/device/reset-location', 'POST');
      applySimulationState(res.simulation);
      toast('已恢复 iPhone 真实 GPS 定位', 'success');
    } catch (err) {
      toast(err.message, 'error');
    }
  });

  $('btnRevealDevMode').addEventListener('click', async () => {
    const { udid } = selectedDeviceParams();
    try {
      const res = await api('/api/device/reveal-dev-mode', 'POST', { udid });
      toast(res.message, 'success');
    } catch (err) {
      toast(err.message, 'error');
    }
  });

  $('btnPairWifi').addEventListener('click', async () => {
    const { udid } = selectedDeviceParams();
    try {
      const res = await api('/api/device/pair-wifi', 'POST', { udid });
      toast(res.message, 'success');
      await refreshDevices(true);
    } catch (err) {
      toast(err.message, 'error');
    }
  });

  $('btnMountDdi').addEventListener('click', async () => {
    $('btnMountDdi').disabled = true;
    $('btnMountDdi').textContent = '挂载中...';
    toast('正在检查/下载并挂载开发者镜像，首次下载约 17MB 请稍候...', 'info');
    try {
      const res = await api('/api/device/mount-ddi', 'POST');
      applyDeviceState(res.device);
      toast('开发者镜像已就绪', 'success');
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      $('btnMountDdi').disabled = false;
      $('btnMountDdi').textContent = '挂载镜像';
    }
  });

  // --- Simulation Controls ---
  function formatDuration(sec) {
    const s = Math.max(0, Math.round(sec || 0));
    const m = Math.floor(s / 60);
    const rem = s % 60;
    return `${String(m).padStart(2, '0')}:${String(rem).padStart(2, '0')}`;
  }

  function applySimulationState(sim) {
    state.simulation = sim || { mode: 'idle', trail: [] };
    const mode = state.simulation.mode || 'idle';

    $('btnStartRun').style.display = mode === 'idle' || mode === 'pinned' ? 'block' : 'none';
    $('btnPauseRun').style.display = mode === 'running' ? 'block' : 'none';
    $('btnResumeRun').style.display = mode === 'paused' ? 'block' : 'none';
    $('btnStopRun').style.display =
      mode === 'running' || mode === 'paused' || mode === 'completed_holding' ? 'block' : 'none';
    $('btnStopPin').style.display = mode === 'pinned' ? 'block' : 'none';

    $('hudPhaseBadge').textContent = state.simulation.phase_label || '待机';
    $('hudMessage').textContent = state.simulation.message || '空闲';
    if (state.simulation.current_lat != null) {
      $('hudCoord').textContent = `WGS84 ${state.simulation.current_lat.toFixed(6)}, ${state.simulation.current_lng.toFixed(6)}`;
    } else {
      $('hudCoord').textContent = '-';
    }
    $('hudDistance').textContent = `${Math.round(state.simulation.distance_m || 0)} m / ${Math.round(state.simulation.target_total_m || 0)} m`;
    $('hudLap').textContent = `${state.simulation.current_lap || 0} / ${state.simulation.total_laps || 0}`;
    const spd = state.simulation.current_speed_mps || 0;
    $('hudPace').textContent = `${formatPace(state.simulation.pace_sec_per_km)} / ${spd.toFixed(1)} m/s`;
    $('hudTime').textContent = `${formatDuration(state.simulation.elapsed_s)} / ${formatDuration(state.simulation.eta_s)}`;
    $('hudProgress').style.width = `${Math.min(100, Math.max(0, state.simulation.progress_pct || 0))}%`;

    const splitsBox = $('hudLapSplits');
    const splits = Array.isArray(state.simulation.lap_stats) ? state.simulation.lap_stats : [];
    if (splits.length > 0) {
      splitsBox.style.display = 'flex';
      splitsBox.innerHTML = '';
      splits.forEach((sp) => {
        const chip = document.createElement('span');
        chip.className = 'hud-lap-chip';
        chip.textContent = `第${sp.lap}圈 ${formatPace(sp.pace_sec_per_km)} (${sp.avg_speed_mps}m/s)`;
        splitsBox.appendChild(chip);
      });
    } else {
      splitsBox.style.display = 'none';
    }

    renderSimulationOverlay();
  }

  $('btnStartRun').addEventListener('click', async () => {
    if (state.waypointsWgs.length < 2) {
      toast('请先在右侧地图点击至少 2 个点（或点“6点自由跑道”/“规则跑道生成器”）', 'warn');
      return;
    }
    try {
      const res = await api('/api/run/start', 'POST', {
        points: state.waypointsWgs,
        closed_loop: state.routeMode === 'loop',
        route_mode: state.routeMode,
        speed_mps: parseFloat($('inpSpeedMps').value) || 3.0,
        mode_limit: $('selLimitMode').value,
        laps: parseInt($('inpLaps').value, 10) || 5,
        target_distance_m: parseFloat($('inpTargetDistance').value) || 2000,
        interval_s: parseFloat($('inpInterval').value) || 1.0,
        speed_jitter_pct: parseFloat($('inpSpeedJitter').value) || 0,
        gps_drift_m: parseFloat($('inpGpsDrift').value) || 0,
        smooth_curves: $('chkSmoothRun').checked,
        pace_profile: $('selPaceProfile').value,
        fatigue_pct: parseFloat($('inpFatiguePct').value) || 0,
        end_sprint: $('chkEndSprint').checked,
        shuttle_lane_offset_m: parseFloat($('inpShuttleOffset').value) || 1.8,
      });
      applySimulationState(res.simulation);
      toast('已开始模拟校园跑', 'success');
    } catch (err) {
      toast(err.message, 'error');
    }
  });

  $('btnPauseRun').addEventListener('click', async () => {
    const res = await api('/api/run/pause', 'POST');
    applySimulationState(res.simulation);
  });

  $('btnResumeRun').addEventListener('click', async () => {
    const res = await api('/api/run/resume', 'POST');
    applySimulationState(res.simulation);
  });

  $('btnStopRun').addEventListener('click', async () => {
    const res = await api('/api/run/stop', 'POST', { reset_location: false });
    applySimulationState(res.simulation);
    toast('已停止模拟（如需清除虚拟定位请点右上角【一键恢复真实定位】）');
  });

  $('btnLockPin').addEventListener('click', async () => {
    if (!state.pinWgs) {
      toast('请先点击地图选点或输入经纬度', 'warn');
      return;
    }
    try {
      const res = await api('/api/pin/set', 'POST', {
        lat: state.pinWgs[0],
        lng: state.pinWgs[1],
        crs: 'wgs84',
        micro_drift_m: parseFloat($('inpPinDrift').value) || 0,
      });
      applySimulationState(res.simulation);
      toast('已锁定定点定位，可在手机上签到打卡', 'success');
    } catch (err) {
      toast(err.message, 'error');
    }
  });

  $('btnStopPin').addEventListener('click', async () => {
    const res = await api('/api/pin/stop', 'POST', { reset_location: true });
    applySimulationState(res.simulation);
    toast('已解除定点锁定并恢复真实 GPS', 'success');
  });

  document.querySelectorAll('button[data-nudge]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const step = parseFloat($('selNudgeStep').value) || 15;
      const dir = btn.dataset.nudge;
      const north = dir === 'n' ? step : dir === 's' ? -step : 0;
      const east = dir === 'e' ? step : dir === 'w' ? -step : 0;
      if (state.simulation.mode !== 'pinned') {
        if (!state.pinWgs) return;
        // Preview nudge locally when not locked
        const dLat = (north / 6371008.8) * (180 / Math.PI);
        const dLng = (east / (6371008.8 * Math.cos((state.pinWgs[0] * Math.PI) / 180))) * (180 / Math.PI);
        setPinFromWgs(state.pinWgs[0] + dLat, state.pinWgs[1] + dLng, false);
        return;
      }
      try {
        const res = await api('/api/pin/nudge', 'POST', { north_m: north, east_m: east });
        applySimulationState(res.simulation);
        if (res.simulation.current_lat != null) {
          setPinFromWgs(res.simulation.current_lat, res.simulation.current_lng, false);
        }
      } catch (err) {
        toast(err.message, 'error');
      }
    });
  });

  // --- Place Search & Direct Coordinate Jump ---
  function hideSearchResults() {
    const resBox = $('searchResults');
    if (resBox) resBox.style.display = 'none';
  }

  function updateClearSearchVisibility() {
    const hasVal = Boolean($('inpMapSearch').value.trim()) || $('searchResults').style.display === 'block';
    $('btnClearSearch').style.display = hasVal ? 'inline-flex' : 'none';
  }

  $('inpMapSearch').addEventListener('input', updateClearSearchVisibility);

  $('btnClearSearch').addEventListener('click', () => {
    $('inpMapSearch').value = '';
    hideSearchResults();
    updateClearSearchVisibility();
    $('inpMapSearch').focus();
  });

  async function performSearch() {
    const q = $('inpMapSearch').value.trim();
    const resBox = $('searchResults');
    hideSearchResults();
    updateClearSearchVisibility();
    if (!q) return;

    // Check direct "lat, lng" input
    const coordMatch = q.match(/^(-?\d+(?:\.\d+)?)\s*[,，\s]\s*(-?\d+(?:\.\d+)?)$/);
    if (coordMatch) {
      const lat = parseFloat(coordMatch[1]);
      const lng = parseFloat(coordMatch[2]);
      if (lat >= -90 && lat <= 90 && lng >= -180 && lng <= 180) {
        map.setView(wgsToMap(lat, lng), 17);
        if (state.activeTab === 'pin') setPinFromWgs(lat, lng, false);
        toast(`已飞往 WGS-84 (${lat.toFixed(5)}, ${lng.toFixed(5)})`);
        return;
      }
    }

    $('btnMapSearch').disabled = true;
    $('btnMapSearch').textContent = '搜索中...';
    try {
      const items = await api(`/api/geocode?q=${encodeURIComponent(q)}`);
      if (!items.length) {
        toast('未搜索到地点，可尝试加上城市/校区名或直接输入“纬度,经度”', 'warn');
        return;
      }
      resBox.innerHTML = '';

      const header = document.createElement('div');
      header.className = 'search-results-header';
      header.innerHTML = `<span>找到 ${items.length} 个相关地点（点击即可飞往）</span>`;
      const closeBtn = document.createElement('button');
      closeBtn.id = 'btnCloseSearchResults';
      closeBtn.className = 'btn btn-sm';
      closeBtn.textContent = '× 关闭';
      closeBtn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        hideSearchResults();
        updateClearSearchVisibility();
      });
      header.appendChild(closeBtn);
      resBox.appendChild(header);

      items.forEach((item) => {
        const div = document.createElement('div');
        div.className = 'search-result-item';
        const titleRow = document.createElement('div');
        titleRow.className = 'search-result-title';
        const nameSpan = document.createElement('span');
        nameSpan.textContent = item.name;
        const srcSpan = document.createElement('span');
        srcSpan.className = 'search-source-tag';
        srcSpan.textContent = item.source || '地图';
        titleRow.appendChild(nameSpan);
        titleRow.appendChild(srcSpan);

        const addrDiv = document.createElement('div');
        addrDiv.className = 'search-result-addr';
        addrDiv.textContent = item.address || `${Number(item.lat).toFixed(5)}, ${Number(item.lng).toFixed(5)}`;

        div.appendChild(titleRow);
        div.appendChild(addrDiv);
        div.addEventListener('click', () => {
          hideSearchResults();
          updateClearSearchVisibility();
          map.setView(wgsToMap(item.lat, item.lng), 17);
          if (state.activeTab === 'pin') setPinFromWgs(item.lat, item.lng, false);
          toast(`已定位到：${item.name}`, 'success');
        });
        resBox.appendChild(div);
      });
      resBox.style.display = 'block';
      updateClearSearchVisibility();
    } catch {
      toast('在线地名搜索暂时不可用，请直接拖拽地图或输入“纬度,经度”', 'warn');
    } finally {
      $('btnMapSearch').disabled = false;
      $('btnMapSearch').textContent = '跳转';
    }
  }

  document.addEventListener('click', (ev) => {
    const bar = document.querySelector('.map-search-bar');
    const resBox = $('searchResults');
    if (resBox.style.display !== 'block') return;
    if ((bar && bar.contains(ev.target)) || resBox.contains(ev.target)) return;
    hideSearchResults();
    updateClearSearchVisibility();
  });

  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') {
      hideSearchResults();
      updateClearSearchVisibility();
    }
  });

  $('btnMapSearch').addEventListener('click', performSearch);
  $('inpMapSearch').addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') performSearch();
  });

  $('selBaseMap').addEventListener('change', (ev) => {
    setBaseMap(ev.target.value);
  });

  // --- Environment Detection & Onboarding Guide ---
  const GUIDE_STORAGE_KEY = 'ios27fakerun_guide_v11_dismissed';

  async function openExternalUrl(url) {
    try {
      await api('/api/system/open-url', 'POST', { url });
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  function makeEnvCard({ title, statusClass, statusText, desc, actions = [] }) {
    const card = document.createElement('div');
    card.className = `env-card ${statusClass}`;
    card.innerHTML = `
      <div class="env-card-head">
        <span></span>
        <span class="env-status-tag"></span>
      </div>
      <div class="env-card-desc"></div>
      <div class="env-card-actions"></div>
    `;
    card.querySelector('.env-card-head span:first-child').textContent = title;
    card.querySelector('.env-status-tag').textContent = statusText;
    card.querySelector('.env-card-desc').textContent = desc;
    const actBox = card.querySelector('.env-card-actions');
    actions.forEach((act) => {
      const btn = document.createElement('button');
      btn.className = act.primary ? 'btn-primary btn-sm' : 'btn btn-sm';
      btn.textContent = act.label;
      btn.addEventListener('click', act.onClick);
      actBox.appendChild(btn);
    });
    return card;
  }

  async function runEnvironmentCheck(openIfMissing = false) {
    try {
      const [env, devs] = await Promise.all([
        api('/api/env-check'),
        api('/api/devices').catch(() => ({ usb: [], wifi: [] })),
      ]);
      state.envInfo = env;

      const driverOk = env.apple_driver && env.apple_driver.status === 'ok';
      $('envAlertDot').style.display = driverOk ? 'none' : 'block';
      $('envWarningBanner').style.display = driverOk ? 'none' : 'flex';
      if (!driverOk && env.apple_driver) {
        $('envBannerTitle').textContent = `⚠️ ${env.apple_driver.label}`;
        $('envBannerDesc').textContent = env.apple_driver.detail;
      }

      const box = $('envCardsContainer');
      if (box) {
        box.innerHTML = '';
        const drv = env.apple_driver || {};
        box.appendChild(
          makeEnvCard({
            title: '1. Apple 手机驱动',
            statusClass: drv.status === 'ok' ? 'ok' : 'err',
            statusText: drv.status === 'ok' ? '已就绪' : drv.status === 'stopped' ? '未启动' : '缺失',
            desc: drv.detail || '',
            actions:
              drv.status === 'ok'
                ? []
                : [
                    {
                      label: '微软商店装 Apple 设备 (推荐)',
                      primary: true,
                      onClick: () => openExternalUrl(drv.store_url),
                    },
                    {
                      label: '下载 iTunes 64位',
                      onClick: () => openExternalUrl(drv.itunes_url),
                    },
                  ],
          })
        );

        const wv2 = env.webview2 || {};
        box.appendChild(
          makeEnvCard({
            title: '2. Edge WebView2 运行库',
            statusClass: wv2.status === 'ok' ? 'ok' : 'warn',
            statusText: wv2.status === 'ok' ? '已就绪' : '建议安装',
            desc: wv2.label || '',
            actions:
              wv2.status === 'ok'
                ? []
                : [
                    {
                      label: '下载 WebView2 运行库',
                      primary: true,
                      onClick: () => openExternalUrl(wv2.download_url),
                    },
                  ],
          })
        );

        const usbList = devs.usb || [];
        const firstUsb = usbList[0];
        box.appendChild(
          makeEnvCard({
            title: '3. iPhone USB 连接',
            statusClass: usbList.length > 0 ? 'ok' : 'warn',
            statusText: usbList.length > 0 ? `已识别 ${usbList.length} 台` : '未检测到',
            desc: firstUsb
              ? `${firstUsb.name} (iOS ${firstUsb.ios_version || '?'})`
              : '请用支持传数据的线连接 iPhone，解锁手机并在弹窗点“信任此电脑”。',
          })
        );

        const devMode = firstUsb ? firstUsb.developer_mode : null;
        box.appendChild(
          makeEnvCard({
            title: '4. iPhone 开发者模式',
            statusClass: devMode === true ? 'ok' : devMode === false ? 'err' : 'warn',
            statusText: devMode === true ? '已开启' : devMode === false ? '未开启' : '待连接检测',
            desc:
              devMode === true
                ? '开发者模式已开启，可直接点击顶部【连接设备】开始使用！'
                : devMode === false
                ? '请在手机【设置 → 隐私与安全性 → 开发者模式】中开启并重启手机。'
                : '连上 iPhone 后会自动检测开发者模式状态。',
            actions:
              devMode === false
                ? [
                    {
                      label: '显示开发者模式开关',
                      primary: true,
                      onClick: () => $('btnRevealDevMode').click(),
                    },
                  ]
                : [],
          })
        );
      }

      if (openIfMissing && !driverOk) {
        openGuideModal(0);
      }
      return env;
    } catch {
      return null;
    }
  }

  function setGuideStep(idx) {
    const total = 5;
    state.guideStep = Math.max(0, Math.min(total - 1, idx));
    document.querySelectorAll('.guide-step-pill').forEach((p) => {
      p.classList.toggle('active', Number(p.dataset.step) === state.guideStep);
    });
    document.querySelectorAll('.guide-pane').forEach((pane) => {
      pane.classList.toggle('active', Number(pane.dataset.guidePane) === state.guideStep);
    });
    $('btnGuidePrev').disabled = state.guideStep === 0;
    $('btnGuideNext').textContent = state.guideStep === total - 1 ? '开始使用 ✓' : '下一步';
  }

  function openGuideModal(step = 0) {
    setGuideStep(step);
    $('chkDontShowGuide').checked = localStorage.getItem(GUIDE_STORAGE_KEY) === '1';
    $('guideModal').classList.add('open');
    runEnvironmentCheck(false);
  }

  document.querySelectorAll('.guide-step-pill').forEach((pill) => {
    pill.addEventListener('click', () => setGuideStep(Number(pill.dataset.step)));
  });
  $('btnGuidePrev').addEventListener('click', () => setGuideStep(state.guideStep - 1));
  $('btnGuideNext').addEventListener('click', () => {
    if (state.guideStep >= 4) {
      $('guideModal').classList.remove('open');
    } else {
      setGuideStep(state.guideStep + 1);
    }
  });
  $('chkDontShowGuide').addEventListener('change', (ev) => {
    localStorage.setItem(GUIDE_STORAGE_KEY, ev.target.checked ? '1' : '0');
  });
  $('btnOpenGuide').addEventListener('click', () => openGuideModal(0));
  $('btnOpenEnvCheck').addEventListener('click', () => openGuideModal(0));
  $('btnCloseGuide').addEventListener('click', () => $('guideModal').classList.remove('open'));
  $('guideModal').addEventListener('click', (ev) => {
    if (ev.target === $('guideModal')) $('guideModal').classList.remove('open');
  });
  $('btnGuideRecheck').addEventListener('click', async () => {
    await runEnvironmentCheck(false);
    await refreshDevices(true);
    toast('环境与设备状态已刷新');
  });
  $('btnGuideRevealDev').addEventListener('click', () => $('btnRevealDevMode').click());

  $('btnBannerAppleDevices').addEventListener('click', () => {
    const drv = (state.envInfo && state.envInfo.apple_driver) || {};
    openExternalUrl(drv.store_url || 'https://apps.microsoft.com/detail/9np83lwlpz9k');
  });
  $('btnBannerItunes').addEventListener('click', () => {
    const drv = (state.envInfo && state.envInfo.apple_driver) || {};
    openExternalUrl(drv.itunes_url || 'https://www.apple.com/itunes/download/win64');
  });
  $('btnBannerRecheck').addEventListener('click', async () => {
    const env = await runEnvironmentCheck(false);
    await refreshDevices(true);
    if (env && env.ready) toast('Apple 驱动已就绪！', 'success');
    else toast('仍未检测到运行中的 Apple 驱动，请安装后重试', 'warn');
  });
  $('btnBannerDismiss').addEventListener('click', () => {
    $('envWarningBanner').style.display = 'none';
  });

  // --- Logs & LAN modal ---
  function renderLogs() {
    const body = $('logsBody');
    body.innerHTML = '';
    state.logs.slice(-100).forEach((entry) => {
      const div = document.createElement('div');
      div.className = `log-line ${entry.level || 'info'}`;
      div.textContent = `[${entry.time}] [${(entry.level || 'INFO').toUpperCase()}] ${entry.message}`;
      body.appendChild(div);
    });
    body.scrollTop = body.scrollHeight;
  }

  $('btnOpenLogs').addEventListener('click', () => {
    renderLogs();
    $('logsModal').classList.add('open');
  });
  $('btnCloseLogs').addEventListener('click', () => $('logsModal').classList.remove('open'));
  $('logsModal').addEventListener('click', (ev) => {
    if (ev.target === $('logsModal')) $('logsModal').classList.remove('open');
  });

  // --- WebSocket live sync ---
  function connectWebSocket() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${proto}//${location.host}/ws`);
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === 'snapshot') {
          applyDeviceState(msg.data.device);
          applySimulationState(msg.data.simulation);
          state.logs = msg.data.logs || [];
          renderLogs();
        } else if (msg.type === 'device') {
          applyDeviceState(msg.data);
        } else if (msg.type === 'simulation') {
          applySimulationState(msg.data);
        } else if (msg.type === 'log') {
          state.logs.push(msg.data);
          if (state.logs.length > 150) state.logs.shift();
          if (!state.device.connected && msg.data && msg.data.message) {
            $('deviceStatusText').textContent = msg.data.message;
          }
          if ($('logsModal').classList.contains('open')) renderLogs();
        }
      } catch {
        // ignore malformed frame
      }
    };
    ws.onclose = () => {
      setTimeout(connectWebSocket, 2000);
    };
  }

  // --- Boot ---
  async function init() {
    setBaseMap('amap_sat_label');
    try {
      const snap = await api('/api/state');
      applyDeviceState(snap.device);
      applySimulationState(snap.simulation);
      state.logs = snap.logs || [];
      const urls = snap.lan_urls || [];
      $('lanUrlsBox').textContent = urls.length ? urls.join('  |  ') : '（当前未检测到局域网 IP）';

      const last = snap.last_state || {};
      if (last.route_mode) {
        setRouteMode(last.route_mode);
      } else if (last.closed_loop === false) {
        setRouteMode('open');
      }
      if (Array.isArray(last.route_points) && last.route_points.length) {
        state.waypointsWgs = last.route_points;
        renderRouteOverlay();
        renderWaypointTable();
        const bounds = L.latLngBounds(state.waypointsWgs.map(([lat, lng]) => wgsToMap(lat, lng)));
        map.fitBounds(bounds, { padding: [40, 40], maxZoom: 18 });
      }
      if (Array.isArray(last.last_pin) && last.last_pin.length === 2) {
        setPinFromWgs(last.last_pin[0], last.last_pin[1], false);
      }
      if (last.speed_mps) {
        $('inpSpeedMps').value = last.speed_mps;
        $('inpPaceStr').value = formatPace(1000 / last.speed_mps);
      }
      if (last.pace_profile) $('selPaceProfile').value = last.pace_profile;
      if (last.fatigue_pct !== undefined) $('inpFatiguePct').value = last.fatigue_pct;
      if (last.end_sprint !== undefined) $('chkEndSprint').checked = Boolean(last.end_sprint);
      updatePacePreview();
      if (last.laps) $('inpLaps').value = last.laps;
      if (last.target_distance_m) $('inpTargetDistance').value = last.target_distance_m;
    } catch {
      // ignore boot errors
    }

    await refreshDevices(true);
    await loadSavedLists();
    connectWebSocket();

    const env = await runEnvironmentCheck(false);
    const dismissed = localStorage.getItem(GUIDE_STORAGE_KEY) === '1';
    if ((env && !env.ready) || !dismissed) {
      openGuideModal(0);
    }
  }

  init();
})();
