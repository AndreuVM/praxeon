/**
 * PRAXEON Decision Tree Layout Engine
 * Computes deterministic 2D hierarchical positions (x, y) for decision trees,
 * providing clean horizontal branching (bifurcations) and backtracks.
 */

export function computeTreeLayout(nodes) {
  if (!nodes || nodes.length === 0) return [];

  const NODE_WIDTH = 205;
  const NODE_HEIGHT = 44;
  const START_WIDTH = 80;
  const HORIZONTAL_GAP = 35; // Gap between sibling branch cards
  const VERTICAL_STEP = 80;  // Vertical distance between tree levels
  const ROOT_CENTER_X = 420; // Default center coordinate

  // 1. Build lookup maps
  const nodeMap = {};
  const childrenMap = {};

  nodes.forEach((node) => {
    nodeMap[node.id] = { ...node };
    childrenMap[node.id] = [];
  });

  // 2. Identify root and wire up parent-child relations
  let rootId = 'start';
  nodes.forEach((node) => {
    const pid = node.parentId;
    if (!pid || pid === node.id || (!nodeMap[pid] && (pid === 'start' || pid.startsWith('root')))) {
      if (node.id === 'start' || !rootId) {
        rootId = node.id;
      }
    } else if (nodeMap[pid]) {
      childrenMap[pid].push(node.id);
    } else {
      // If parent is not in map but starts with root, attach to start
      if (nodeMap['start']) {
        childrenMap['start'].push(node.id);
        nodeMap[node.id].parentId = 'start';
      }
    }
  });

  if (!nodeMap[rootId] && nodes.length > 0) {
    rootId = nodes[0].id;
  }

  // 3. Assign depths (Y coordinate)
  function assignDepths(id, currentDepth) {
    if (!nodeMap[id]) return;
    nodeMap[id].depth = currentDepth;
    nodeMap[id].y = currentDepth === 0 ? 30 : 30 + currentDepth * VERTICAL_STEP;

    const children = childrenMap[id] || [];
    children.forEach((childId) => {
      assignDepths(childId, currentDepth + 1);
    });
  }
  assignDepths(rootId, 0);

  // 4. Measure required subtree width (in pixels)
  function getSubtreeWidth(id) {
    const children = childrenMap[id] || [];
    if (children.length === 0) {
      return id === 'start' ? START_WIDTH : NODE_WIDTH;
    }

    if (children.length === 1) {
      return Math.max(NODE_WIDTH, getSubtreeWidth(children[0]));
    }

    let totalWidth = 0;
    children.forEach((childId, idx) => {
      totalWidth += getSubtreeWidth(childId);
      if (idx > 0) totalWidth += HORIZONTAL_GAP;
    });
    return Math.max(NODE_WIDTH, totalWidth);
  }

  // 5. Position subtrees recursively
  function positionSubtree(id, leftX, availableWidth) {
    if (!nodeMap[id]) return;

    const isStart = id === 'start' || nodeMap[id].type === 'start';
    const currentWidth = isStart ? START_WIDTH : NODE_WIDTH;

    // Center this node within its allocated subtree horizontal span
    const nodeCenterX = leftX + availableWidth / 2;
    nodeMap[id].x = Math.round(nodeCenterX - currentWidth / 2);

    const children = childrenMap[id] || [];
    if (children.length === 0) return;

    if (children.length === 1) {
      // Single child: directly inherit vertical column alignment
      positionSubtree(children[0], leftX, availableWidth);
      return;
    }

    // Multiple children: distribute their subtrees from left to right
    let currentLeft = leftX;
    const totalRequired = children.reduce((sum, cid, idx) => {
      return sum + getSubtreeWidth(cid) + (idx > 0 ? HORIZONTAL_GAP : 0);
    }, 0);

    // If available width is wider than required, center the children block
    if (availableWidth > totalRequired) {
      currentLeft += (availableWidth - totalRequired) / 2;
    }

    children.forEach((childId) => {
      const childSubtreeWidth = getSubtreeWidth(childId);
      positionSubtree(childId, currentLeft, childSubtreeWidth);
      currentLeft += childSubtreeWidth + HORIZONTAL_GAP;
    });
  }

  const rootSubtreeWidth = getSubtreeWidth(rootId);
  const rootSpanLeft = ROOT_CENTER_X + (START_WIDTH / 2) - (rootSubtreeWidth / 2);
  const effectiveLeft = Math.max(30, rootSpanLeft);

  positionSubtree(rootId, effectiveLeft, rootSubtreeWidth);

  // Return nodes array maintaining original order but with refreshed layout coordinates
  return nodes.map((n) => nodeMap[n.id] || n);
}
