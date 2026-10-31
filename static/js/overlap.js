/**
 * 重叠课程分道（lane）算法：同一列里时间重叠的课并排显示。
 * 按开始时间扫描成簇，簇内贪心占用最早空闲的车道。
 */

export function layoutItems(items) {
  if (!Array.isArray(items) || items.length === 0) return [];

  const sorted = items
    .map((item) => ({ ...item, end: Math.max(Number(item.end) || 0, (Number(item.start) || 0) + 1) }))
    .sort((a, b) => a.start - b.start || b.end - a.end);

  let cluster = [];
  let clusterEnd = -Infinity;

  const flush = () => {
    const laneEnds = [];
    for (const item of cluster) {
      let lane = laneEnds.findIndex((end) => end <= item.start);
      if (lane < 0) {
        lane = laneEnds.length;
        laneEnds.push(0);
      }
      laneEnds[lane] = item.end;
      item.lane = lane;
    }
    for (const item of cluster) item.lanes = Math.max(1, laneEnds.length);
    cluster = [];
  };

  for (const item of sorted) {
    if (cluster.length > 0 && item.start >= clusterEnd) {
      flush();
      clusterEnd = -Infinity;
    }
    cluster.push(item);
    clusterEnd = Math.max(clusterEnd, item.end);
  }
  flush();
  return sorted;
}
