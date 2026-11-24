// China coordinate transformations (WGS-84 / GCJ-02 / BD-09) and geodetic helpers
// Mirrors backend/coords.py so map clicks and markers stay 100% aligned with iOS WGS-84.

const Coords = (() => {
  const PI = Math.PI;
  const X_PI = (PI * 3000.0) / 180.0;
  const A = 6378245.0;
  const EE = 0.00669342162296594323;
  const EARTH_R = 6371008.8;

  function outOfChina(lat, lng) {
    return !(lng >= 72.004 && lng <= 137.8347 && lat >= 0.8293 && lat <= 55.8271);
  }

  function transformLat(x, y) {
    let ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x));
    ret += ((20.0 * Math.sin(6.0 * x * PI) + 20.0 * Math.sin(2.0 * x * PI)) * 2.0) / 3.0;
    ret += ((20.0 * Math.sin(y * PI) + 40.0 * Math.sin((y / 3.0) * PI)) * 2.0) / 3.0;
    ret += ((160.0 * Math.sin((y / 12.0) * PI) + 320.0 * Math.sin((y * PI) / 30.0)) * 2.0) / 3.0;
    return ret;
  }

  function transformLng(x, y) {
    let ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x));
    ret += ((20.0 * Math.sin(6.0 * x * PI) + 20.0 * Math.sin(2.0 * x * PI)) * 2.0) / 3.0;
    ret += ((20.0 * Math.sin(x * PI) + 40.0 * Math.sin((x / 3.0) * PI)) * 2.0) / 3.0;
    ret += ((150.0 * Math.sin((x / 12.0) * PI) + 300.0 * Math.sin((x / 30.0) * PI)) * 2.0) / 3.0;
    return ret;
  }

  function gcjDelta(lat, lng) {
    let dLat = transformLat(lng - 105.0, lat - 35.0);
    let dLng = transformLng(lng - 105.0, lat - 35.0);
    const radLat = (lat / 180.0) * PI;
    let magic = Math.sin(radLat);
    magic = 1.0 - EE * magic * magic;
    const sqrtMagic = Math.sqrt(magic);
    dLat = (dLat * 180.0) / (((A * (1.0 - EE)) / (magic * sqrtMagic)) * PI);
    dLng = (dLng * 180.0) / ((A / sqrtMagic) * Math.cos(radLat) * PI);
    return [dLat, dLng];
  }

  function wgs84ToGcj02(lat, lng) {
    if (outOfChina(lat, lng)) return [lat, lng];
    const [dLat, dLng] = gcjDelta(lat, lng);
    return [lat + dLat, lng + dLng];
  }

  function gcj02ToWgs84(lat, lng) {
    if (outOfChina(lat, lng)) return [lat, lng];
    let wLat = lat;
    let wLng = lng;
    for (let i = 0; i < 10; i++) {
      const [gLat, gLng] = wgs84ToGcj02(wLat, wLng);
      const dLat = gLat - lat;
      const dLng = gLng - lng;
      wLat -= dLat;
      wLng -= dLng;
      if (Math.abs(dLat) < 1e-10 && Math.abs(dLng) < 1e-10) break;
    }
    return [wLat, wLng];
  }

  function gcj02ToBd09(lat, lng) {
    const z = Math.hypot(lng, lat) + 0.00002 * Math.sin(lat * X_PI);
    const theta = Math.atan2(lat, lng) + 0.000003 * Math.cos(lng * X_PI);
    return [z * Math.sin(theta) + 0.006, z * Math.cos(theta) + 0.0065];
  }

  function bd09ToGcj02(lat, lng) {
    const x = lng - 0.0065;
    const y = lat - 0.006;
    const z = Math.hypot(x, y) - 0.00002 * Math.sin(y * X_PI);
    const theta = Math.atan2(y, x) - 0.000003 * Math.cos(x * X_PI);
    return [z * Math.sin(theta), z * Math.cos(theta)];
  }

  function toWgs84(lat, lng, crs) {
    if (crs === 'gcj02') return gcj02ToWgs84(lat, lng);
    if (crs === 'bd09') {
      const [gLat, gLng] = bd09ToGcj02(lat, lng);
      return gcj02ToWgs84(gLat, gLng);
    }
    return [lat, lng];
  }

  function fromWgs84(lat, lng, crs) {
    if (crs === 'gcj02') return wgs84ToGcj02(lat, lng);
    if (crs === 'bd09') {
      const [gLat, gLng] = wgs84ToGcj02(lat, lng);
      return gcj02ToBd09(gLat, gLng);
    }
    return [lat, lng];
  }

  function haversineDistance(lat1, lng1, lat2, lng2) {
    const p1 = (lat1 * PI) / 180.0;
    const p2 = (lat2 * PI) / 180.0;
    const dp = ((lat2 - lat1) * PI) / 180.0;
    const dl = ((lng2 - lng1) * PI) / 180.0;
    const a = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
    return 2.0 * EARTH_R * Math.asin(Math.min(1.0, Math.sqrt(a)));
  }

  function routeLengthMeters(points, closed = false) {
    if (!points || points.length < 2) return 0;
    let total = 0;
    for (let i = 1; i < points.length; i++) {
      total += haversineDistance(points[i - 1][0], points[i - 1][1], points[i][0], points[i][1]);
    }
    if (closed) {
      const first = points[0];
      const last = points[points.length - 1];
      if (first[0] !== last[0] || first[1] !== last[1]) {
        total += haversineDistance(last[0], last[1], first[0], first[1]);
      }
    }
    return total;
  }

  return {
    wgs84ToGcj02,
    gcj02ToWgs84,
    gcj02ToBd09,
    bd09ToGcj02,
    toWgs84,
    fromWgs84,
    haversineDistance,
    routeLengthMeters,
  };
})();
