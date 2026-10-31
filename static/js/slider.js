/**
 * 滑块拼图的拖动记录：与学校页面插件（longbow.slidercaptcha.js）的采样规则一致，
 * 只是把用户本人的拖动原样记下来交给学校校验，不生成、不修饰轨迹。
 *
 * 坐标一律用学校插件的 280 宽坐标系；页面上缩放显示时由调用方先换算。
 */

export const SLIDER_WIDTH = 280;
export const SLIDER_HEIGHT = 155;
export const KNOB_WIDTH = 40;

/**
 * 拼图块在 280 宽画布上的显示宽度（插件：小图原宽 × 画布宽 / 大图原宽 − 2）。
 * 学校目前是 590 宽背景配 93 宽拼图条 → 约 42px。
 */
export function pieceWidth(bgNaturalWidth, pieceNaturalWidth, width = SLIDER_WIDTH) {
  if (!(bgNaturalWidth > 0) || !(pieceNaturalWidth > 0)) return 0;
  return Math.max(0, (pieceNaturalWidth * width) / bgNaturalWidth - 2);
}

export function createTrackRecorder({ width = SLIDER_WIDTH, minInterval = 20, minDistance = 2, now = () => Date.now() } = {}) {
  let originX = 0;
  let originY = 0;
  let lastAt = 0;
  let last = null;
  let tracks = [];
  let active = false;

  return {
    get active() {
      return active;
    },

    start(x, y) {
      originX = x;
      originY = y;
      tracks = [{ a: 0, b: 0, c: 0 }];
      last = null;
      lastAt = now();
      active = true;
    },

    /** 返回拼图/滑块应处的横向位移；越界时返回 null（保持原位，不记录）。 */
    move(x, y) {
      if (!active) return null;
      const dx = x - originX;
      const dy = y - originY;
      if (dx < 0 || dx + KNOB_WIDTH > width) return null;
      const t = now();
      const dt = t - lastAt;
      if (dt < minInterval) return dx;
      const point = { a: dx, b: dy, c: dt };
      if (last && Math.hypot(point.a - last.a, point.b - last.b) < minDistance) return dx;
      tracks.push(point);
      last = point;
      lastAt = t;
      return dx;
    },

    /** 松手：没有移动或往回拖到起点左边时返回 null（不提交）。 */
    end(x, y) {
      if (!active) return null;
      active = false;
      const dx = x - originX;
      if (dx === 0 || dx < 0) return null;
      tracks.push({ a: dx, b: y - originY, c: now() - lastAt });
      // 插件松手时直接上报 clientX 差值，不截断
      return { canvasLength: width, moveLength: dx, tracks: tracks.slice() };
    },

    cancel() {
      active = false;
    },
  };
}
