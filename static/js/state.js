// 全局应用状态（唯一可变共享对象）
export const state = {
  token: null,
  snap: null,
  inv: null,
  settings: null,
  filter: { svc: "all", task: "all" },
  knownPorts: new Set(),
  firstSnap: true,
  prevUnits: {},
  feed: [],
  feedCleared: false,
  otherPortsOpen: false,
  resTab: "cpu",
  logTimer: null,
  logUnitId: null,
  editingUnitId: null,
  claimPid: null,
  paletteItems: [],     // 命令面板完整条目池（不被搜索过滤覆盖）
  paletteFiltered: [],  // 当前搜索词下的可见条目
  paletteIndex: 0,
  lcUnitId: null,
  lcTimer: null,
  dragId: null,
  notifiedExit: new Set(),
};

// 卡片图标 / 徽章配色
export const PALETTE_COLORS = ["#3b82f6", "#22c55e", "#f59e0b", "#a78bfa", "#22d3ee", "#f472b6", "#ef4444", "#60a5fa"];
