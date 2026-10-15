import React, { useState, useEffect, useRef, useMemo } from 'react';
import {
  Workflow,
  ShieldCheck,
  CheckCircle2,
  AlertTriangle,
  GitBranch,
  GitFork,
  GitMerge,
  Play,
  RotateCcw,
  SkipForward,
  Plus,
  Trash2,
  X,
  Bot,
  Terminal,
  Save,
  Info,
  ArrowRight,
  Flag,
  Cpu,
  Settings2,
  LayoutTemplate,
  Check,
  Focus,
} from 'lucide-react';
import NodeFields from '../workflow/NodeFields';
import ConditionBuilder from '../workflow/ConditionBuilder';
import {
  NODE_TYPE_LABELS,
  TOOL_CHOICES,
  WORK_TYPES,
  conditionToForm,
  emptyCondition,
  formToCondition,
  formToNodeUpdate,
  nodeToForm,
} from '../workflow/formHelpers';
import {
  fetchWorkflows,
  setWorkflowActive,
  getWorkflowExecution,
  approveExecutionNode,
  resolveFailedNode,
  createWorkflow,
  getWorkflowDetail,
  addWorkflowNode,
  updateWorkflowNode,
  updateWorkflowNodePosition,
  deleteWorkflowNode,
  connectWorkflowNodes,
  updateWorkflowEdge,
  deleteWorkflowEdge,
  validateWorkflow,
  executeWorkflow,
  stepWorkflow,
  backtrackWorkflow,
  resetWorkflow,
  approveWorkflowNode,
  fetchCanonicalTemplates,
  instantiateCanonicalTemplate,
  fetchAgents,
} from '../../services/api';
import { notify, confirmDialog } from '../Dialogs';

export default function WorkflowsView({ session: _session = {}, events: _events = [], followExecution = null }) {
  const [workflows, setWorkflows] = useState([]);
  const [activeWorkflowId, setActiveWorkflowId] = useState(null);
  const [workflow, setWorkflow] = useState(null);
  const [_loading, setLoading] = useState(true);
  const [connectionError, setConnectionError] = useState(null);
  const [executing, setExecuting] = useState(false);
  const [validationResult, setValidationResult] = useState(null);
  const [logs, setLogs] = useState([
    { time: new Date().toTimeString().split(' ')[0], tag: 'INIT', msg: 'Editor visual de workflows inicializado.' },
  ]);

  // Agentes registrados en el sistema
  const [registeredAgents, setRegisteredAgents] = useState([]);

  // Selección activa
  const [selectedNodeId, setSelectedNodeId] = useState(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState(null);

  // Modo cableado interactivo (conectar puertos haciendo clic)
  const [wiringSourceId, setWiringSourceId] = useState(null);

  // Formularios de edición en el inspector
  const [nodeEditForm, setNodeEditForm] = useState(null);
  const [edgeEditForm, setEdgeEditForm] = useState(null);

  // Modales
  const [showCreateWfModal, setShowCreateWfModal] = useState(false);
  const [showAddNodeModal, setShowAddNodeModal] = useState(false);
  const [showConnectModal, setShowConnectModal] = useState(false);
  const [showTemplatesModal, setShowTemplatesModal] = useState(false);
  const [canonicalTemplates, setCanonicalTemplates] = useState([]);

  // Formularios de creación
  const [newWfName, setNewWfName] = useState('');
  const [newWfDesc, setNewWfDesc] = useState('');
  const [createNodeForm, setCreateNodeForm] = useState({
    name: '',
    node_type: 'AGENT',
    agent_id: '',
    tool_name: '',
    x: 350,
    y: 200,
  });
  const [connectForm, setConnectForm] = useState({
    from_node: '',
    to_node: '',
    label: '',
    has_condition: false,
    field: 'output.status',
    operator: '==',
    expected_value: 'SUCCESS',
  });

  // Dragging state para lienzo
  const [draggingNodeId, setDraggingNodeId] = useState(null);
  const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 });
  const canvasRef = useRef(null);

  const addLog = (tag, msg) => {
    const time = new Date().toTimeString().split(' ')[0];
    setLogs((prev) => [...prev.slice(-30), { time, tag, msg }]);
  };

  // Cargar lista de agentes registrados
  const loadAgents = async () => {
    try {
      const res = await fetchAgents();
      if (res && res.data) {
        // El API identifica al agente con `agent_id`; los formularios usan `id` (sin esto el desplegable
        // guardaba el texto visible como id y el nodo no encontraba a su agente).
        const list = Array.isArray(res.data) ? res.data : Object.values(res.data);
        setRegisteredAgents(list.map((a) => ({ ...a, id: a.agent_id || a.id })));
      }
    } catch (err) {
      console.warn('No se pudieron cargar los agentes registrados:', err);
    }
  };

  // Cargar plantillas canónicas del sistema
  const loadTemplates = async () => {
    try {
      const res = await fetchCanonicalTemplates();
      if (res && res.data) {
        setCanonicalTemplates(res.data);
      }
    } catch (err) {
      console.warn('No se pudieron cargar las plantillas canónicas:', err);
    }
  };

  // Instanciar una plantilla canónica
  const handleInstantiateTemplate = async (templateKey, templateName) => {
    try {
      const res = await instantiateCanonicalTemplate(templateKey, `${templateName} (Instancia)`);
      setShowTemplatesModal(false);
      addLog('TEMPLATE', `Plantilla '${templateName}' instanciada exitosamente.`);
      await loadWorkflows();
      if (res.data?.workflow_id) {
        setActiveWorkflowId(res.data.workflow_id);
      }
    } catch (err) {
      notify(`Error al instanciar plantilla: ${err.message}`);
      addLog('ERROR', `Fallo al instanciar plantilla: ${err.message}`);
    }
  };

  // Cargar lista de workflows
  const loadWorkflows = async () => {
    try {
      setLoading(true);
      const res = await fetchWorkflows();
      if (res && res.data) {
        setWorkflows(res.data);
        setConnectionError(null);
        if (!activeWorkflowId && res.data.length > 0) {
          setActiveWorkflowId(res.data[0].workflow_id);
        }
      }
    } catch (err) {
      console.error('Error al listar workflows:', err);
      setConnectionError(err.message);
      addLog('ERROR', `Error al listar workflows: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  // Cargar workflow activo
  const loadActiveWorkflow = async (wfId) => {
    if (!wfId) return;
    try {
      const res = await getWorkflowDetail(wfId);
      if (res && res.data) {
        setWorkflow(res.data);
        addLog('WORKFLOW', `Cargado '${res.data.name}' (${Object.keys(res.data.nodes || {}).length} nodos).`);
      }
    } catch (err) {
      console.error(`Error al cargar workflow ${wfId}:`, err);
      addLog('ERROR', `Fallo al cargar flujo ${wfId}: ${err.message}`);
    }
  };

  useEffect(() => {
    loadAgents();
    loadWorkflows();
    loadTemplates();
  }, []);

  useEffect(() => {
    if (activeWorkflowId) {
      loadActiveWorkflow(activeWorkflowId);
      setSelectedNodeId(null);
      setSelectedEdgeId(null);
      setWiringSourceId(null);
    }
  }, [activeWorkflowId]);

  // Ejecución de una misión gobernada por este workflow: el lienzo muestra su avance nodo a nodo
  const [liveExecution, setLiveExecution] = useState(null);
  const followId = followExecution?.executionId || null;
  const followWorkflowId = followExecution?.workflowId || null;
  useEffect(() => {
    if (!followId) {
      setLiveExecution(null);
      return undefined;
    }
    if (followWorkflowId) setActiveWorkflowId(followWorkflowId);
    let stopped = false;
    const poll = async () => {
      try {
        const res = await getWorkflowExecution(followId);
        if (!stopped && res?.data) setLiveExecution(res.data);
      } catch {
        /* la ejecución aún no existe o terminó: se reintenta en el siguiente ciclo */
      }
    };
    poll();
    const timer = setInterval(poll, 1000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [followId, followWorkflowId]);

  // Nodos normalizados como Array siempre (con el estado en vivo de la misión si se está siguiendo una)
  const nodesArray = useMemo(() => {
    if (!workflow || !workflow.nodes) return [];
    const base = Array.isArray(workflow.nodes) ? workflow.nodes : Object.values(workflow.nodes);
    if (!liveExecution || liveExecution.workflow_id !== workflow.workflow_id) return base;
    const states = liveExecution.node_states || {};
    const outputs = liveExecution.node_outputs || {};
    return base.map((n) => {
      const nid = n.node_id || n.id;
      return { ...n, status: states[nid] || 'PENDING', outputs: outputs[nid] || {} };
    });
  }, [workflow, liveExecution]);

  const pendingFailure = liveExecution?.variables?._pending_failure || null;
  const handleResolveFailure = async (action) => {
    if (!pendingFailure) return;
    try {
      await resolveFailedNode(followId, pendingFailure.node_id, action);
      addLog('WORKFLOW', `Nodo '${pendingFailure.node_id}': decisión '${action}'.`);
    } catch (err) {
      addLog('ERROR', `No se pudo aplicar la decisión: ${err.message}`);
    }
  };

  // Aristas normalizadas como Array
  const edgesArray = useMemo(() => {
    if (!workflow || !workflow.edges) return [];
    return Array.isArray(workflow.edges) ? workflow.edges : [];
  }, [workflow]);

  // Dimensiones dinámicas del espacio del lienzo para permitir scroll infinito y colocación libre
  const canvasDimensions = useMemo(() => {
    let maxX = 4200;
    let maxY = 3200;
    nodesArray.forEach((n) => {
      const x = (n.position?.x ?? 100) + 500;
      const y = (n.position?.y ?? 100) + 400;
      if (x > maxX) maxX = x;
      if (y > maxY) maxY = y;
    });
    return { width: maxX, height: maxY };
  }, [nodesArray]);

  // Centrar vista suavemente sobre el grupo de nodos
  const handleCenterView = () => {
    if (!canvasRef.current) return;
    if (nodesArray.length > 0) {
      const minX = Math.min(...nodesArray.map((n) => n.position?.x ?? 100));
      const minY = Math.min(...nodesArray.map((n) => n.position?.y ?? 100));
      canvasRef.current.scrollTo({
        left: Math.max(0, minX - 100),
        top: Math.max(0, minY - 100),
        behavior: 'smooth',
      });
    } else {
      canvasRef.current.scrollTo({ left: 0, top: 0, behavior: 'smooth' });
    }
  };

  // Sincronizar formulario de edición de nodo cuando cambia la selección
  useEffect(() => {
    if (!selectedNodeId) {
      setNodeEditForm(null);
      return;
    }
    const node = nodesArray.find((n) => (n.node_id || n.id) === selectedNodeId);
    if (node) {
      setNodeEditForm(nodeToForm(node, nodesArray.map((n) => n.node_id || n.id)));
      setSelectedEdgeId(null);
    }
  }, [selectedNodeId, nodesArray]);

  // Sincronizar formulario de edición de arista cuando cambia la selección
  useEffect(() => {
    if (!selectedEdgeId) {
      setEdgeEditForm(null);
      return;
    }
    const edge = edgesArray.find((e) => e.edge_id === selectedEdgeId);
    if (edge) {
      setEdgeEditForm({
        edge_id: edge.edge_id,
        from_node: edge.from_node,
        to_node: edge.to_node,
        label: edge.label || '',
        has_condition: Boolean(edge.condition),
        cond: conditionToForm(edge.condition, nodesArray.map((n) => n.node_id || n.id)),
      });
      setSelectedNodeId(null);
    }
  }, [selectedEdgeId, edgesArray]);

  // Manejador para crear un nuevo flujo
  const handleCreateWorkflow = async (e) => {
    e.preventDefault();
    if (!newWfName.trim()) return;
    try {
      const res = await createWorkflow({
        name: newWfName.trim(),
        description: newWfDesc.trim(),
      });
      setShowCreateWfModal(false);
      setNewWfName('');
      setNewWfDesc('');
      addLog('CREATE', `Workflow '${res.data.name}' creado exitosamente.`);
      await loadWorkflows();
      if (res.data?.workflow_id) {
        setActiveWorkflowId(res.data.workflow_id);
      }
    } catch (err) {
      notify(`Error al crear flujo: ${err.message}`);
    }
  };

  // Manejador para añadir un nodo nuevo
  const handleAddNode = async (e) => {
    e.preventDefault();
    if (!activeWorkflowId) {
      notify('No hay un workflow seleccionado. Selecciona o crea un workflow primero, y comprueba que el backend esté ejecutándose.');
      return;
    }
    if (!createNodeForm.name.trim()) {
      notify('Introduce un nombre para el nuevo nodo.');
      return;
    }
    try {
      const payload = {
        name: createNodeForm.name.trim(),
        node_type: createNodeForm.node_type,
        x: Number(createNodeForm.x) || 300,
        y: Number(createNodeForm.y) || 200,
        agent_id: WORK_TYPES.includes(createNodeForm.node_type) ? (createNodeForm.agent_id.trim() || null) : null,
        tool_name: createNodeForm.node_type === 'TASK' ? (createNodeForm.tool_name.trim() || null) : null,
      };
      const res = await addWorkflowNode(activeWorkflowId, payload);
      setShowAddNodeModal(false);
      setCreateNodeForm({ name: '', node_type: 'AGENT', agent_id: '', tool_name: '', x: 350, y: 200 });
      addLog('NODE', `Nodo '${payload.name}' (${payload.node_type}) añadido.`);
      await loadActiveWorkflow(activeWorkflowId);
      if (res.data?.node_id) {
        setSelectedNodeId(res.data.node_id);
      }
    } catch (err) {
      notify(`Error al añadir nodo: ${err.message}`);
      addLog('ERROR', `Fallo al añadir nodo: ${err.message}`);
    }
  };

  // Crear nodo rápido desde la paleta de componentes
  const handleQuickAddNode = (type, defaultName) => {
    if (!activeWorkflowId) {
      notify('No hay ningún workflow seleccionado o activo. Comprueba que el servidor backend (puerto 8000) esté en ejecución y crea o selecciona un workflow.');
      return;
    }
    setCreateNodeForm({
      name: defaultName,
      node_type: type,
      agent_id: WORK_TYPES.includes(type) && registeredAgents.length > 0 ? registeredAgents[0].id : '',
      tool_name: '',
      x: 200 + Math.floor(Math.random() * 200),
      y: 150 + Math.floor(Math.random() * 150),
    });
    setShowAddNodeModal(true);
  };

  // Guardar cambios del nodo seleccionado
  const handleSaveNodeProperties = async (e) => {
    e.preventDefault();
    if (!activeWorkflowId || !nodeEditForm) return;
    try {
      const update = formToNodeUpdate(nodeEditForm);
      if (['WHILE', 'IF', 'DECISION'].includes(nodeEditForm.node_type) && !formToCondition(nodeEditForm.cond)) {
        notify('Indica qué dato del resultado se debe comprobar en la condición.');
        return;
      }
      await updateWorkflowNode(activeWorkflowId, nodeEditForm.node_id, update);

      addLog('NODE', `Propiedades de nodo '${nodeEditForm.name}' actualizadas.`);
      await loadActiveWorkflow(activeWorkflowId);
    } catch (err) {
      notify(`Error al actualizar nodo: ${err.message}`);
    }
  };

  // Guardar cambios de la arista / condición de unión seleccionada
  const handleSaveEdgeProperties = async (e) => {
    e.preventDefault();
    if (!activeWorkflowId || !edgeEditForm) return;
    try {
      let condition = null;
      if (edgeEditForm.has_condition) {
        condition = formToCondition(edgeEditForm.cond);
        if (!condition) {
          notify('Indica qué dato del resultado se debe comprobar en la condición.');
          return;
        }
      }

      await updateWorkflowEdge(activeWorkflowId, edgeEditForm.edge_id, {
        label: edgeEditForm.label.trim(),
        condition: condition,
      });

      addLog('EDGE', `Conexión '${edgeEditForm.edge_id}' actualizada (${condition ? 'con condición' : 'sin condición'}).`);
      await loadActiveWorkflow(activeWorkflowId);
    } catch (err) {
      notify(`Error al actualizar unión: ${err.message}`);
    }
  };

  // Eliminar nodo seleccionado
  const handleDeleteNode = async (nodeId) => {
    if (!activeWorkflowId) return;
    const targetNode = nodesArray.find((n) => (n.node_id || n.id) === nodeId);
    const nodeName = targetNode?.name || nodeId;
    if (!(await confirmDialog(`¿Eliminar el nodo «${nodeName}» y todas sus conexiones?`, { confirmLabel: 'Eliminar', danger: true }))) return;
    try {
      await deleteWorkflowNode(activeWorkflowId, nodeId);
      if (selectedNodeId === nodeId) setSelectedNodeId(null);
      if (wiringSourceId === nodeId) setWiringSourceId(null);
      addLog('NODE', `Nodo '${nodeName}' eliminado.`);
      await loadActiveWorkflow(activeWorkflowId);
    } catch (err) {
      notify(`Error al eliminar nodo: ${err.message}`);
    }
  };

  // Eliminar arista seleccionada
  const handleDeleteEdge = async (edgeId) => {
    if (!activeWorkflowId || !(await confirmDialog('¿Eliminar esta conexión?', { confirmLabel: 'Eliminar', danger: true }))) return;
    try {
      await deleteWorkflowEdge(activeWorkflowId, edgeId);
      if (selectedEdgeId === edgeId) setSelectedEdgeId(null);
      addLog('EDGE', `Conexión '${edgeId}' eliminada.`);
      await loadActiveWorkflow(activeWorkflowId);
    } catch (err) {
      notify(`Error al eliminar arista: ${err.message}`);
    }
  };

  // Cableado interactivo directo (Clic en puerto salida -> Clic en nodo destino)
  const handlePortClick = async (e, nodeId, isOutput) => {
    e.stopPropagation();
    if (isOutput) {
      // Iniciar cableado
      if (wiringSourceId === nodeId) {
        setWiringSourceId(null); // Cancelar
      } else {
        setWiringSourceId(nodeId);
        addLog('WIRE', `Iniciado cableado desde nodo '${nodeId}'. Haz clic en el destino.`);
      }
    } else {
      // Puerto de entrada clicado
      if (wiringSourceId && wiringSourceId !== nodeId) {
        await completeConnection(wiringSourceId, nodeId);
      }
    }
  };

  // Completar conexión directa
  const completeConnection = async (fromId, toId) => {
    if (!activeWorkflowId || fromId === toId) return;
    try {
      const fromNode = nodesArray.find((n) => (n.node_id || n.id) === fromId);
      const toNode = nodesArray.find((n) => (n.node_id || n.id) === toId);
      const label = `${fromNode?.name || fromId} → ${toNode?.name || toId}`;

      const res = await connectWorkflowNodes(activeWorkflowId, {
        from_node: fromId,
        to_node: toId,
        label: label,
        condition: null,
      });

      addLog('EDGE', `Unión creada: ${label}`);
      setWiringSourceId(null);
      await loadActiveWorkflow(activeWorkflowId);
      if (res.data?.edge_id) {
        setSelectedEdgeId(res.data.edge_id);
      }
    } catch (err) {
      notify(`Error al conectar nodos: ${err.message}`);
      setWiringSourceId(null);
    }
  };

  // Conectar mediante modal
  const handleConnectNodesModal = async (e) => {
    e.preventDefault();
    if (!activeWorkflowId || !connectForm.from_node || !connectForm.to_node) return;
    try {
      let condition = null;
      if (connectForm.has_condition) {
        let expectedVal = connectForm.expected_value;
        if (connectForm.operator === 'is_true' || connectForm.operator === 'is_false') {
          expectedVal = null;
        } else if (expectedVal === 'true') {
          expectedVal = true;
        } else if (expectedVal === 'false') {
          expectedVal = false;
        } else if (!isNaN(Number(expectedVal)) && expectedVal.trim() !== '') {
          expectedVal = Number(expectedVal);
        }

        condition = {
          field: connectForm.field.trim() || 'output.status',
          operator: connectForm.operator,
          expected_value: expectedVal,
        };
      }

      const res = await connectWorkflowNodes(activeWorkflowId, {
        from_node: connectForm.from_node,
        to_node: connectForm.to_node,
        label: connectForm.label.trim() || `${connectForm.from_node} → ${connectForm.to_node}`,
        condition: condition,
      });

      setShowConnectModal(false);
      setConnectForm({
        from_node: '',
        to_node: '',
        label: '',
        has_condition: false,
        field: 'output.status',
        operator: '==',
        expected_value: 'SUCCESS',
      });
      addLog('EDGE', `Conexión establecida.`);
      await loadActiveWorkflow(activeWorkflowId);
      if (res.data?.edge_id) {
        setSelectedEdgeId(res.data.edge_id);
      }
    } catch (err) {
      notify(`Error al conectar nodos: ${err.message}`);
    }
  };

  // Acciones de Ejecución y Backtracking
  const handleValidate = async () => {
    if (!activeWorkflowId) return;
    try {
      const res = await validateWorkflow(activeWorkflowId);
      setValidationResult(res.data);
      if (res.data?.is_valid) {
        addLog('VALID', `DAG Válido. Orden topológico: [${(res.data.topological_order || []).join(' → ')}]`);
      } else {
        addLog('INVALID', `Errores: ${(res.data?.errors || []).join(', ')}`);
      }
    } catch (err) {
      setValidationResult({ is_valid: false, errors: [err.message] });
      addLog('ERROR', `Fallo al validar: ${err.message}`);
    }
  };

  const handleExecute = async () => {
    if (!activeWorkflowId) return;
    setExecuting(true);
    addLog('RUN', 'Iniciando ejecución supervisada del workflow...');
    try {
      const res = await executeWorkflow(activeWorkflowId);
      addLog('COMPLETE', `Workflow completado con estado: ${res.data?.status || 'COMPLETED'}`);
      await loadActiveWorkflow(activeWorkflowId);
    } catch (err) {
      notify(`Error en ejecución: ${err.message}`);
      addLog('ERROR', `Fallo en ejecución: ${err.message}`);
    } finally {
      setExecuting(false);
    }
  };

  const handleStep = async () => {
    if (!activeWorkflowId) return;
    try {
      const res = await stepWorkflow(activeWorkflowId);
      addLog('STEP', `Paso ejecutado. Nodo: '${res.data?.executed_node_id || 'Ninguno'}' | Estado: ${res.data?.status}`);
      await loadActiveWorkflow(activeWorkflowId);
    } catch (err) {
      notify(`Error al avanzar paso: ${err.message}`);
      addLog('ERROR', `Error en step: ${err.message}`);
    }
  };

  const handleBacktrack = async (targetNodeId) => {
    if (!activeWorkflowId) return;
    try {
      const res = await backtrackWorkflow(activeWorkflowId, targetNodeId);
      addLog('BACKTRACK', `Retroceso determinista al nodo '${targetNodeId}'. Éxito: ${res.data?.success}`);
      await loadActiveWorkflow(activeWorkflowId);
    } catch (err) {
      notify(`Error en retroceso determinista: ${err.message}`);
      addLog('ERROR', `Error en backtrack: ${err.message}`);
    }
  };

  const handleReset = async () => {
    if (!activeWorkflowId) return;
    try {
      await resetWorkflow(activeWorkflowId);
      addLog('RESET', 'Workflow reiniciado a estado IDLE.');
      await loadActiveWorkflow(activeWorkflowId);
    } catch (err) {
      notify(`Error al reiniciar workflow: ${err.message}`);
    }
  };

  const handleApproveNode = async (nodeId, approved) => {
    if (!activeWorkflowId) return;
    try {
      const comment = approved ? 'Aprobado desde supervisor visual' : 'Rechazado desde supervisor visual';
      if (liveExecution && liveExecution.workflow_id === activeWorkflowId) {
        await approveExecutionNode(followId, nodeId, approved, comment);
      } else {
        await approveWorkflowNode(activeWorkflowId, nodeId, approved, comment);
      }
      addLog('APPROVAL', `Nodo '${nodeId}' ${approved ? 'APROBADO' : 'RECHAZADO'}.`);
      await loadActiveWorkflow(activeWorkflowId);
    } catch (err) {
      notify(`Error al procesar aprobación: ${err.message}`);
      addLog('ERROR', `Fallo al aprobar nodo: ${err.message}`);
    }
  };


  // Drag and drop interactivo en lienzo nativo con soporte de scroll y espacio infinito
  const draggingStateRef = useRef({
    nodeId: null,
    offset: { x: 0, y: 0 },
    currentPosition: { x: 0, y: 0 },
  });

  const handleMouseDownNode = (e, nodeId, currentX, currentY) => {
    e.stopPropagation();
    if (e.target.classList.contains('port-circle')) return;
    const canvasEl = canvasRef.current;
    if (!canvasEl) return;

    const canvasRect = canvasEl.getBoundingClientRect();
    const scrollLeft = canvasEl.scrollLeft || 0;
    const scrollTop = canvasEl.scrollTop || 0;
    const mouseCanvasX = e.clientX - canvasRect.left + scrollLeft;
    const mouseCanvasY = e.clientY - canvasRect.top + scrollTop;

    const offset = {
      x: mouseCanvasX - currentX,
      y: mouseCanvasY - currentY,
    };

    setDraggingNodeId(nodeId);
    setDragOffset(offset);
    draggingStateRef.current = {
      nodeId,
      offset,
      currentPosition: { x: currentX, y: currentY },
    };
  };

  const handleMouseMoveCanvas = (e) => {
    const { nodeId, offset } = draggingStateRef.current;
    if (!nodeId || !canvasRef.current) return;

    const canvasEl = canvasRef.current;
    const canvasRect = canvasEl.getBoundingClientRect();

    // Auto-scroll fluido al acercarse a los márgenes visibles de la ventana
    const edgeMargin = 45;
    const scrollSpeed = 18;
    if (e.clientX > canvasRect.right - edgeMargin) {
      canvasEl.scrollLeft += scrollSpeed;
    } else if (e.clientX < canvasRect.left + edgeMargin && canvasEl.scrollLeft > 0) {
      canvasEl.scrollLeft -= scrollSpeed;
    }
    if (e.clientY > canvasRect.bottom - edgeMargin) {
      canvasEl.scrollTop += scrollSpeed;
    } else if (e.clientY < canvasRect.top + edgeMargin && canvasEl.scrollTop > 0) {
      canvasEl.scrollTop -= scrollSpeed;
    }

    const mouseCanvasX = e.clientX - canvasRect.left + canvasEl.scrollLeft;
    const mouseCanvasY = e.clientY - canvasRect.top + canvasEl.scrollTop;

    const rawX = mouseCanvasX - offset.x;
    const rawY = mouseCanvasY - offset.y;

    // Permitir colocación libre en todo el lienzo amplio
    const maxX = Math.max(canvasDimensions.width - 240, 4000);
    const maxY = Math.max(canvasDimensions.height - 140, 3000);
    const newX = Math.max(20, Math.min(maxX, rawX));
    const newY = Math.max(20, Math.min(maxY, rawY));

    draggingStateRef.current.currentPosition = { x: newX, y: newY };

    setWorkflow((prev) => {
      if (!prev) return prev;
      if (Array.isArray(prev.nodes)) {
        return {
          ...prev,
          nodes: prev.nodes.map((n) =>
            (n.node_id || n.id) === nodeId ? { ...n, position: { x: newX, y: newY } } : n
          ),
        };
      }
      return {
        ...prev,
        nodes: {
          ...prev.nodes,
          [nodeId]: {
            ...prev.nodes[nodeId],
            position: { x: newX, y: newY },
          },
        },
      };
    });
  };

  const handleMouseUpCanvas = async () => {
    const { nodeId, currentPosition } = draggingStateRef.current;
    if (nodeId && activeWorkflowId) {
      try {
        await updateWorkflowNodePosition(activeWorkflowId, nodeId, currentPosition.x, currentPosition.y);
      } catch (err) {
        console.error('Error al persistir posición del nodo:', err);
      }
    }
    draggingStateRef.current = { nodeId: null, offset: { x: 0, y: 0 }, currentPosition: { x: 0, y: 0 } };
    setDraggingNodeId(null);
  };

  // Escuchar eventos globales para garantizar arrastre ininterrumpido
  useEffect(() => {
    if (!draggingNodeId) return;

    const onGlobalMouseMove = (e) => {
      handleMouseMoveCanvas(e);
    };

    const onGlobalMouseUp = () => {
      handleMouseUpCanvas();
    };

    window.addEventListener('mousemove', onGlobalMouseMove);
    window.addEventListener('mouseup', onGlobalMouseUp);

    return () => {
      window.removeEventListener('mousemove', onGlobalMouseMove);
      window.removeEventListener('mouseup', onGlobalMouseUp);
    };
  }, [draggingNodeId, activeWorkflowId, canvasDimensions]);

  // Estilo e icono según tipo de nodo
  const getNodeTypeMeta = (type) => {
    const base = { color: 'var(--muted)', bg: 'transparent', border: 'var(--line)' };
    switch (type) {
      case 'AGENT': return { ...base, icon: <Bot size={13} />, label: 'Agente' };
      case 'TASK': return { ...base, icon: <Terminal size={13} />, label: 'Tarea o herramienta' };
      case 'DECISION':
      case 'IF': return { ...base, icon: <GitBranch size={13} />, label: 'Decisión (IF)' };
      case 'WHILE': return { ...base, icon: <RotateCcw size={13} />, label: 'Bucle WHILE' };
      case 'DELEGATE': return { ...base, icon: <Bot size={13} />, label: 'Router (delegación)' };
      case 'HUMAN_APPROVAL': return { ...base, icon: <ShieldCheck size={13} />, label: 'Aprobación humana' };
      case 'PARALLEL_FORK': return { ...base, icon: <GitFork size={13} />, label: 'Bifurcación paralela' };
      case 'PARALLEL_JOIN': return { ...base, icon: <GitMerge size={13} />, label: 'Unión (sincronización)' };
      case 'START': return { ...base, icon: <Play size={13} />, label: 'Inicio' };
      case 'END': return { ...base, icon: <Flag size={13} />, label: 'Fin' };
      default: return { ...base, icon: <Cpu size={13} />, label: type };
    }
  };

  const NODE_STATUS_LABEL = {
    PENDING: 'Pendiente',
    READY: 'Listo',
    RUNNING: 'En curso',
    COMPLETED: 'Completado',
    FAILED: 'Fallido',
    SKIPPED: 'Omitido',
    WAITING_APPROVAL: 'Esperando aprobación',
    WAITING_RESULT: 'Esperando resultado',
    RETRYING: 'Reintentando',
  };

  const getNodeStatusBadge = (status) => {
    switch (status) {
      case 'COMPLETED':
        return { color: 'var(--ok)', bg: 'color-mix(in srgb, var(--ok) 20%, transparent)' };
      case 'RUNNING':
        return { color: 'var(--accent)', bg: 'color-mix(in srgb, var(--accent) 20%, transparent)' };
      case 'FAILED':
        return { color: 'var(--bad)', bg: 'color-mix(in srgb, var(--bad) 20%, transparent)' };
      case 'SKIPPED':
        return { color: 'var(--muted)', bg: 'color-mix(in srgb, var(--muted) 20%, transparent)' };
      case 'READY':
        return { color: 'var(--accent)', bg: 'color-mix(in srgb, var(--accent) 20%, transparent)' };
      case 'WAITING_APPROVAL':
        return { color: 'var(--muted)', bg: 'color-mix(in srgb, var(--muted) 20%, transparent)' };
      case 'WAITING_RESULT':
        return { color: 'var(--muted)', bg: 'color-mix(in srgb, var(--muted) 20%, transparent)' };
      case 'RETRYING':
        return { color: 'var(--accent)', bg: 'color-mix(in srgb, var(--accent) 20%, transparent)' };
      default:
        return { color: 'var(--muted)', bg: 'color-mix(in srgb, var(--muted) 20%, transparent)' };
    }
  };



  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        flex: 1,
        height: '100%',
        backgroundColor: 'var(--bg)',
        color: 'var(--text)',
        overflow: 'hidden',
        position: 'relative',
      }}
    >
      {liveExecution && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '10px',
            padding: '6px 14px',
            backgroundColor: pendingFailure ? 'color-mix(in srgb, var(--bad) 12%, transparent)' : 'color-mix(in srgb, var(--accent) 10%, transparent)',
            borderBottom: `1px solid ${pendingFailure ? 'color-mix(in srgb, var(--bad) 40%, transparent)' : 'color-mix(in srgb, var(--accent) 30%, transparent)'}`,
            fontSize: '12.5px',
            color: pendingFailure ? 'var(--bad)' : 'var(--accent)',
          }}
        >
          <span style={{ fontWeight: 700 }}>Misión en curso con este flujo · {liveExecution.status}</span>
          {pendingFailure && (
            <>
              <span style={{ flex: 1 }}>
                El nodo «{pendingFailure.node_id}» falló: {String(pendingFailure.error || '').slice(0, 200)}
              </span>
              {['retry', 'skip', 'abort'].map((action) => (
                <button
                  key={action}
                  onClick={() => handleResolveFailure(action)}
                  style={{
                    padding: '3px 10px',
                    borderRadius: '6px',
                    border: '1px solid var(--line)',
                    backgroundColor: action === 'abort' ? 'var(--line)' : 'var(--line)',
                    color: 'var(--text)',
                    fontSize: '11px',
                    cursor: 'pointer',
                  }}
                >
                  {action === 'retry' ? 'Reintentar' : action === 'skip' ? 'Omitir nodo' : 'Abortar'}
                </button>
              ))}
            </>
          )}
        </div>
      )}

      {/* Barra superior: flujo activo y controles de ejecución */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '10px 18px',
          borderBottom: '1px solid var(--line)',
          flexShrink: 0,
          gap: '12px',
          flexWrap: 'wrap',
          zIndex: 40,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
          <h1 style={{ fontSize: 'var(--fs-title)', fontWeight: 600, letterSpacing: '-0.01em', marginRight: '6px' }}>Flujos</h1>

          <select aria-label="Flujo" value={activeWorkflowId || ''} onChange={(e) => setActiveWorkflowId(e.target.value)} style={{ maxWidth: '280px' }}>
            {workflows.map((wf) => (
              <option key={wf.workflow_id} value={wf.workflow_id}>
                {wf.active ? '● ' : ''}
                {wf.name} ({wf.node_count ?? Object.keys(wf.nodes || {}).length} nodos)
              </option>
            ))}
          </select>

          {activeWorkflowId && (() => {
            const current = workflows.find((w) => w.workflow_id === activeWorkflowId);
            const isActive = Boolean(current?.active);
            return (
              <button
                className={`chip${isActive ? '' : ''}`}
                onClick={async () => {
                  try {
                    await setWorkflowActive(activeWorkflowId, !isActive);
                    addLog('INFO', isActive ? 'Workflow desactivado' : 'Workflow activado: ya puede gobernar tareas');
                    await loadWorkflows();
                  } catch (err) {
                    addLog('ERROR', `No se pudo cambiar el estado del workflow: ${err.message}`);
                  }
                }}
                style={{ color: isActive ? 'var(--ok)' : 'var(--muted)', borderColor: isActive ? 'color-mix(in srgb, var(--ok) 45%, transparent)' : 'var(--line)', padding: '4px 11px' }}
                title={isActive ? 'Activo: aparece en el selector de la conversación. Pulsa para desactivar' : 'Inactivo: no se ofrece para las tareas. Pulsa para activar'}
                aria-pressed={isActive}
              >
                <Check size={12} />
                <span>{isActive ? 'Activo' : 'Inactivo'}</span>
              </button>
            );
          })()}

          <button className="btn btn-secondary" onClick={() => setShowCreateWfModal(true)} title="Crear un nuevo flujo de trabajo">
            <Plus size={13} />
            <span>Nuevo flujo</span>
          </button>
          <button
            className="btn btn-secondary"
            onClick={() => {
              loadTemplates();
              setShowTemplatesModal(true);
            }}
            title="Cargar flujos canónicos (revisión de código, redactor y revisor, enrutador de triaje)"
          >
            <LayoutTemplate size={13} />
            <span>Plantillas canónicas</span>
          </button>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          <button className="btn btn-secondary" onClick={handleValidate} title="Validar la causalidad y la ausencia de ciclos">
            <ShieldCheck size={13} />
            <span>Validar DAG</span>
          </button>
          <button className="btn btn-secondary" onClick={handleStep} disabled={executing} title="Avanzar un nodo paso a paso">
            <SkipForward size={13} />
            <span>Paso a paso</span>
          </button>
          <button className="btn btn-secondary" onClick={handleReset} title="Reiniciar la ejecución del flujo">
            <RotateCcw size={13} />
            <span>Reiniciar</span>
          </button>
          <button className="btn btn-secondary" onClick={handleCenterView} title="Centrar la vista sobre los nodos del flujo">
            <Focus size={13} />
            <span>Centrar</span>
          </button>
          <button className="btn btn-primary" onClick={handleExecute} disabled={executing} title="Ejecutar el flujo completo bajo supervisión">
            <Play size={13} className={executing ? 'animate-pulse' : ''} />
            <span>{executing ? 'Ejecutando…' : 'Ejecutar flujo'}</span>
          </button>
        </div>
      </div>

      {/* Banner de Desconexión de Backend */}
      {connectionError && (
        <div
          style={{
            backgroundColor: 'color-mix(in srgb, var(--bad) 15%, transparent)',
            borderBottom: '1px solid color-mix(in srgb, var(--bad) 40%, transparent)',
            padding: '8px 16px',
            fontSize: '12.5px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            color: 'var(--bad)',
            flexShrink: 0,
            zIndex: 35,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <AlertTriangle size={14} color="var(--bad)" />
            <span>
              <strong>Servidor desconectado ({connectionError}):</strong> Comprueba que el servidor PRAXEON se está ejecutando en el puerto 8000 (<code>uv run praxeon-server --port 8000</code>).
            </span>
          </div>
          <button
            onClick={() => loadWorkflows()}
            style={{
              padding: '3px 8px',
              borderRadius: '6px',
              backgroundColor: 'var(--bad)',
              border: '1px solid var(--bad)',
              color: 'var(--on-accent)',
              fontSize: '11px',
              cursor: 'pointer',
              fontWeight: '600',
            }}
          >
            Reintentar la conexión
          </button>
        </div>
      )}

      {/* Banner de Validación */}
      {validationResult && (
        <div
          style={{
            backgroundColor: validationResult.is_valid ? 'color-mix(in srgb, var(--ok) 12%, transparent)' : 'color-mix(in srgb, var(--bad) 12%, transparent)',
            borderBottom: `1px solid ${validationResult.is_valid ? 'color-mix(in srgb, var(--ok) 35%, transparent)' : 'color-mix(in srgb, var(--bad) 35%, transparent)'}`,
            padding: '6px 16px',
            fontSize: '11px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            color: validationResult.is_valid ? 'var(--ok)' : 'var(--bad)',
            flexShrink: 0,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            {validationResult.is_valid ? <CheckCircle2 size={13} /> : <AlertTriangle size={13} />}
            <span>
              {validationResult.is_valid
                ? `El grafo es un DAG válido, sin ciclos. Orden topológico: [${(validationResult.topological_order || []).join(' → ')}]`
                : `Errores en el grafo: ${(validationResult.errors || []).join('; ')}`}
            </span>
          </div>
          <button
            onClick={() => setValidationResult(null)}
            style={{ background: 'none', border: 'none', color: 'inherit', cursor: 'pointer' }}
          >
            <X size={13} />
          </button>
        </div>
      )}

      {/* Banner de Modo Cableado Activo */}
      {wiringSourceId && (
        <div
          style={{
            backgroundColor: 'color-mix(in srgb, var(--accent) 15%, transparent)',
            borderBottom: '1px solid color-mix(in srgb, var(--accent) 40%, transparent)',
            padding: '6px 16px',
            fontSize: '12.5px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            color: 'var(--accent)',
            flexShrink: 0,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <ArrowRight size={14} className="animate-pulse" />
            <span>
              <strong>Modo de cableado:</strong> origen fijado en el nodo <strong>{wiringSourceId}</strong>. Haz clic en el puerto de entrada (izquierda) de otro nodo para crear la unión.
            </span>
          </div>
          <button
            onClick={() => setWiringSourceId(null)}
            style={{
              backgroundColor: 'var(--line)',
              border: '1px solid var(--line)',
              color: 'var(--text)',
              padding: '2px 8px',
              borderRadius: '6px',
              fontSize: '11px',
              cursor: 'pointer',
            }}
          >
            Cancelar el cableado
          </button>
        </div>
      )}

      {/* Contenedor Principal */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden', position: 'relative' }}>
        {/* Paleta Lateral Izquierda de Nodos */}
        <div
          style={{
            width: '175px',
            backgroundColor: 'var(--bg)',
            borderRight: '1px solid var(--line)',
            display: 'flex',
            flexDirection: 'column',
            padding: '12px 10px',
            gap: '8px',
            flexShrink: 0,
            zIndex: 10,
            overflowY: 'auto',
          }}
        >
          <div style={{ fontSize: '11px', fontWeight: '700', textTransform: 'uppercase', color: 'var(--muted)', letterSpacing: '0.05em' }}>
            Paleta de nodos
          </div>

          <button
            onClick={() => handleQuickAddNode('AGENT', 'Agente')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              padding: '7px 8px',
              backgroundColor: 'var(--surface)',
              border: '1px solid var(--line)',
              borderRadius: '9px',
              color: 'var(--text)',
              fontSize: '11px',
              cursor: 'pointer',
              textAlign: 'left',
            }}
            title="Añadir nodo de agente autónomo"
          >
            <div style={{ width: '20px', height: '20px', borderRadius: '6px', backgroundColor: 'transparent', color: 'var(--muted)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <Bot size={12} />
            </div>
            <div>
              <div style={{ fontWeight: '600' }}>Agente</div>
              <div style={{ fontSize: 'var(--fs-label)', color: 'var(--muted)' }}>IA delegada</div>
            </div>
          </button>

          <button
            onClick={() => handleQuickAddNode('TASK', 'Tarea')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              padding: '7px 8px',
              backgroundColor: 'var(--surface)',
              border: '1px solid var(--line)',
              borderRadius: '9px',
              color: 'var(--text)',
              fontSize: '11px',
              cursor: 'pointer',
              textAlign: 'left',
            }}
            title="Añadir tarea o herramienta de ejecución"
          >
            <div style={{ width: '20px', height: '20px', borderRadius: '6px', backgroundColor: 'transparent', color: 'var(--muted)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <Terminal size={12} />
            </div>
            <div>
              <div style={{ fontWeight: '600' }}>Tarea</div>
              <div style={{ fontSize: '11px', color: 'var(--muted)' }}>Herramienta o comando</div>
            </div>
          </button>

          <button
            onClick={() => handleQuickAddNode('DECISION', 'Decisión')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              padding: '7px 8px',
              backgroundColor: 'var(--surface)',
              border: '1px solid var(--line)',
              borderRadius: '9px',
              color: 'var(--text)',
              fontSize: '11px',
              cursor: 'pointer',
              textAlign: 'left',
            }}
            title="Añadir compuerta de decisión condicional"
          >
            <div style={{ width: '20px', height: '20px', borderRadius: '6px', backgroundColor: 'transparent', color: 'var(--muted)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <GitBranch size={12} />
            </div>
            <div>
              <div style={{ fontWeight: '600' }}>Decisión / IF</div>
              <div style={{ fontSize: '11px', color: 'var(--muted)' }}>Bifurcación</div>
            </div>
          </button>

          <button
            onClick={() => handleQuickAddNode('WHILE', 'Bucle de control')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              padding: '7px 8px',
              backgroundColor: 'var(--surface)',
              border: '1px solid var(--line)',
              borderRadius: '9px',
              color: 'var(--text)',
              fontSize: '11px',
              cursor: 'pointer',
              textAlign: 'left',
            }}
            title="Añadir bucle WHILE estructurado con límite de iteraciones"
          >
            <div style={{ width: '20px', height: '20px', borderRadius: '6px', backgroundColor: 'transparent', color: 'var(--muted)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <RotateCcw size={12} />
            </div>
            <div>
              <div style={{ fontWeight: '600' }}>Bucle WHILE</div>
              <div style={{ fontSize: '11px', color: 'var(--muted)' }}>Iteración acotada</div>
            </div>
          </button>

          <button
            onClick={() => handleQuickAddNode('DELEGATE', 'Router de Agentes')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              padding: '7px 8px',
              backgroundColor: 'var(--surface)',
              border: '1px solid var(--line)',
              borderRadius: '9px',
              color: 'var(--text)',
              fontSize: '11px',
              cursor: 'pointer',
              textAlign: 'left',
            }}
            title="Añadir router de delegación dinámica a agentes"
          >
            <div style={{ width: '20px', height: '20px', borderRadius: '6px', backgroundColor: 'transparent', color: 'var(--muted)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <Bot size={12} />
            </div>
            <div>
              <div style={{ fontWeight: '600' }}>Router</div>
              <div style={{ fontSize: '11px', color: 'var(--muted)' }}>Delegación</div>
            </div>
          </button>

          <button
            onClick={() => handleQuickAddNode('HUMAN_APPROVAL', 'Aprobación humana')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              padding: '7px 8px',
              backgroundColor: 'var(--surface)',
              border: '1px solid var(--line)',
              borderRadius: '9px',
              color: 'var(--text)',
              fontSize: '11px',
              cursor: 'pointer',
              textAlign: 'left',
            }}
            title="Añadir pausa supervisada esperando autorización humana"
          >
            <div style={{ width: '20px', height: '20px', borderRadius: '6px', backgroundColor: 'transparent', color: 'var(--muted)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <ShieldCheck size={12} />
            </div>
            <div>
              <div style={{ fontWeight: '600' }}>Aprobación</div>
              <div style={{ fontSize: '11px', color: 'var(--muted)' }}>Supervisión</div>
            </div>
          </button>


          <button
            onClick={() => handleQuickAddNode('PARALLEL_FORK', 'Bifurcación')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              padding: '7px 8px',
              backgroundColor: 'var(--surface)',
              border: '1px solid var(--line)',
              borderRadius: '9px',
              color: 'var(--text)',
              fontSize: '11px',
              cursor: 'pointer',
              textAlign: 'left',
            }}
            title="Añadir bifurcación concurrente en paralelo"
          >
            <div style={{ width: '20px', height: '20px', borderRadius: '6px', backgroundColor: 'transparent', color: 'var(--muted)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <GitFork size={12} />
            </div>
            <div>
              <div style={{ fontWeight: '600' }}>Bifurcación</div>
              <div style={{ fontSize: '11px', color: 'var(--muted)' }}>Paralelo</div>
            </div>
          </button>

          <button
            onClick={() => handleQuickAddNode('PARALLEL_JOIN', 'Unión')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              padding: '7px 8px',
              backgroundColor: 'var(--surface)',
              border: '1px solid var(--line)',
              borderRadius: '9px',
              color: 'var(--text)',
              fontSize: '11px',
              cursor: 'pointer',
              textAlign: 'left',
            }}
            title="Añadir sincronización y unión de ramas"
          >
            <div style={{ width: '20px', height: '20px', borderRadius: '6px', backgroundColor: 'transparent', color: 'var(--muted)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <GitMerge size={12} />
            </div>
            <div>
              <div style={{ fontWeight: '600' }}>Unión</div>
              <div style={{ fontSize: '11px', color: 'var(--muted)' }}>Sincronización</div>
            </div>
          </button>

          <button
            onClick={() => handleQuickAddNode('END', 'Fin')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              padding: '7px 8px',
              backgroundColor: 'var(--surface)',
              border: '1px solid var(--line)',
              borderRadius: '9px',
              color: 'var(--text)',
              fontSize: '11px',
              cursor: 'pointer',
              textAlign: 'left',
            }}
            title="Añadir nodo terminal de finalización"
          >
            <div style={{ width: '20px', height: '20px', borderRadius: '6px', backgroundColor: 'transparent', color: 'var(--muted)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <Flag size={12} />
            </div>
            <div>
              <div style={{ fontWeight: '600' }}>Fin</div>
              <div style={{ fontSize: '11px', color: 'var(--muted)' }}>Terminal</div>
            </div>
          </button>

          <div style={{ marginTop: 'auto', borderTop: '1px solid var(--line)', paddingTop: '10px' }}>
            <button
              onClick={() => setShowConnectModal(true)}
              style={{
                width: '100%',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '6px',
                padding: '6px',
                backgroundColor: 'var(--line)',
                border: '1px solid var(--line)',
                borderRadius: '6px',
                color: 'var(--accent)',
                fontSize: '11px',
                fontWeight: '600',
                cursor: 'pointer',
              }}
            >
              <ArrowRight size={12} />
              <span>Unir nodos</span>
            </button>
          </div>
        </div>

        {/* Lienzo Canvas React con SVG y Nodos Arrastrables */}
        <div
          ref={canvasRef}
          onMouseMove={handleMouseMoveCanvas}
          onMouseUp={handleMouseUpCanvas}
          onClick={() => {
            setSelectedNodeId(null);
            setSelectedEdgeId(null);
          }}
          style={{
            flex: 1,
            position: 'relative',
            backgroundColor: 'var(--bg)',
            overflow: 'auto',
            userSelect: draggingNodeId ? 'none' : 'auto',
          }}
        >
          {/* Espacio Amplio de Trabajo Multidimensional */}
          <div
            style={{
              position: 'relative',
              minWidth: `${canvasDimensions.width}px`,
              minHeight: `${canvasDimensions.height}px`,
              width: `${canvasDimensions.width}px`,
              height: `${canvasDimensions.height}px`,
              backgroundImage: 'radial-gradient(circle, color-mix(in srgb, var(--text) 4%, transparent) 1px, transparent 1px)',
              backgroundSize: '24px 24px',
            }}
          >
            {/* Capa SVG para Aristas y Conexiones */}
            <svg
              style={{
                position: 'absolute',
                inset: 0,
                width: '100%',
                height: '100%',
                pointerEvents: 'none',
              }}
            >
            <defs>
              <marker
                id="wf-arrow"
                viewBox="0 0 10 10"
                refX="6"
                refY="5"
                markerWidth="6"
                markerHeight="6"
                orient="auto-start-reverse"
              >
                <path d="M 0 1 L 8 5 L 0 9 z" fill="var(--muted)" />
              </marker>
              <marker
                id="wf-arrow-selected"
                viewBox="0 0 10 10"
                refX="6"
                refY="5"
                markerWidth="6"
                markerHeight="6"
                orient="auto-start-reverse"
              >
                <path d="M 0 1 L 8 5 L 0 9 z" fill="var(--muted)" />
              </marker>
            </defs>

            {edgesArray.map((edge) => {
              const fromNode = nodesArray.find((n) => (n.node_id || n.id) === edge.from_node);
              const toNode = nodesArray.find((n) => (n.node_id || n.id) === edge.to_node);
              if (!fromNode || !toNode) return null;

              const x1 = (fromNode.position?.x ?? 100) + 210;
              const y1 = (fromNode.position?.y ?? 100) + 40;
              const x2 = toNode.position?.x ?? 300;
              const y2 = (toNode.position?.y ?? 100) + 40;
              const dx = Math.abs(x2 - x1) * 0.5;

              const isSelected = selectedEdgeId === edge.edge_id;
              const strokeColor = isSelected ? 'var(--accent)' : 'var(--muted)';

              return (
                <g
                  key={edge.edge_id || `${edge.from_node}-${edge.to_node}`}
                  style={{ pointerEvents: 'auto', cursor: 'pointer' }}
                  onClick={(e) => {
                    e.stopPropagation();
                    setSelectedEdgeId(edge.edge_id);
                  }}
                >
                  {/* Path invisible ancho para facilitar clic */}
                  <path
                    d={`M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`}
                    fill="none"
                    stroke="transparent"
                    strokeWidth="14"
                  />
                  {/* Path visible */}
                  <path
                    d={`M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`}
                    fill="none"
                    stroke={strokeColor}
                    strokeWidth={isSelected ? 3 : 2}
                    strokeDasharray={edge.condition ? '5 3' : 'none'}
                    markerEnd={isSelected ? 'url(#wf-arrow-selected)' : 'url(#wf-arrow)'}
                    opacity={isSelected ? 1 : 0.85}
                  />

                  {/* Badge con Condición o Etiqueta en el punto medio */}
                  <g transform={`translate(${(x1 + x2) / 2}, ${(y1 + y2) / 2})`}>
                    <rect
                      x="-45"
                      y="-12"
                      width="90"
                      height="20"
                      rx="6"
                      fill={isSelected ? 'var(--line)' : 'var(--surface)'}
                      stroke={strokeColor}
                      strokeWidth="1"
                      opacity="0.9"
                    />
                    <text
                      x="0"
                      y="2"
                      fill={isSelected ? 'var(--accent)' : 'var(--text)'}
                      fontSize="9.5"
                      fontWeight="600"
                      textAnchor="middle"
                      fontFamily="var(--font-mono)"
                    >
                      {edge.condition
                        ? `${edge.condition.field?.split('.').pop() || 'cond'} ${edge.condition.operator} ${edge.condition.expected_value ?? ''}`
                        : (edge.label || 'unión')}
                    </text>
                  </g>
                </g>
              );
            })}
          </svg>

          {/* Capa de Nodos Nativos */}
          {nodesArray.map((node) => {
            const nid = node.node_id || node.id;
            const posX = node.position?.x ?? 100;
            const posY = node.position?.y ?? 100;
            const meta = getNodeTypeMeta(node.node_type);
            const statusBadge = getNodeStatusBadge(node.status);
            const isSelected = selectedNodeId === nid;
            const isWiringSource = wiringSourceId === nid;
            const isDragging = draggingNodeId === nid;

            return (
              <div
                key={nid}
                onClick={(e) => {
                  e.stopPropagation();
                  if (wiringSourceId && wiringSourceId !== nid) {
                    completeConnection(wiringSourceId, nid);
                  } else {
                    setSelectedNodeId(nid);
                  }
                }}
                onMouseDown={(e) => handleMouseDownNode(e, nid, posX, posY)}
                style={{
                  position: 'absolute',
                  left: `${posX}px`,
                  top: `${posY}px`,
                  width: '210px',
                  backgroundColor: 'var(--surface)',
                  border: isWiringSource
                    ? '2px solid var(--accent)'
                    : (isSelected ? '2px solid var(--accent)' : `1.5px solid ${meta.border}`),
                  borderRadius: '9px',
                  padding: '10px 12px',
                  cursor: isDragging ? 'grabbing' : 'grab',
                  boxShadow: isSelected
                    ? 'none'
                    : (isWiringSource ? 'none' : 'none'),
                  zIndex: isDragging ? 30 : (isSelected ? 20 : 5),
                  transition: isDragging ? 'none' : 'border-color 0.15s ease, box-shadow 0.15s ease',
                }}
              >
                {/* Puerto de Entrada (Izquierda) */}
                <div
                  className="port-circle"
                  onClick={(e) => handlePortClick(e, nid, false)}
                  title={wiringSourceId ? `Hacer clic para conectar a este nodo` : `Puerto de entrada de dependencias`}
                  style={{
                    position: 'absolute',
                    left: '-8px',
                    top: '50%',
                    transform: 'translateY(-50%)',
                    width: '14px',
                    height: '14px',
                    borderRadius: '50%',
                    backgroundColor: wiringSourceId ? 'var(--accent)' : 'var(--line)',
                    border: '2px solid var(--line)',
                    cursor: 'pointer',
                    zIndex: 35,
                    boxShadow: wiringSourceId ? 'none' : 'none',
                  }}
                />

                {/* Puerto de Salida (Derecha) */}
                <div
                  className="port-circle"
                  onClick={(e) => handlePortClick(e, nid, true)}
                  title="Arrastrar o hacer clic para crear unión hacia otro nodo"
                  style={{
                    position: 'absolute',
                    right: '-8px',
                    top: '50%',
                    transform: 'translateY(-50%)',
                    width: '14px',
                    height: '14px',
                    borderRadius: '50%',
                    backgroundColor: isWiringSource ? 'var(--accent)' : 'var(--muted)',
                    border: '2px solid var(--line)',
                    cursor: 'pointer',
                    zIndex: 35,
                    boxShadow: 'none',
                  }}
                />

                {/* Encabezado del Nodo */}
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '6px' }}>
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '4px',
                      fontSize: '11px',
                      fontWeight: '700',
                      padding: '2px 6px',
                      borderRadius: '6px',
                      backgroundColor: meta.bg,
                      color: meta.color,
                      border: '1px solid var(--line)',
                    }}
                  >
                    {meta.icon}
                    <span>{meta.label}</span>
                  </div>

                  <span
                    style={{
                      fontSize: '11px',
                      fontWeight: '700',
                      padding: '1px 5px',
                      borderRadius: '6px',
                      backgroundColor: statusBadge.bg,
                      color: statusBadge.color,
                    }}
                  >
                    {NODE_STATUS_LABEL[node.status || 'PENDING'] || node.status}
                  </span>
                </div>

                {/* Título del Nodo */}
                <div style={{ fontSize: '12.5px', fontWeight: '700', color: 'var(--text)', marginBottom: '4px', wordBreak: 'break-word' }}>
                  {node.name}
                </div>

                {/* Metadatos (Agente asignado o Herramienta) */}
                {node.node_type === 'AGENT' && (
                  <div style={{ fontSize: '11px', color: 'var(--ok)', display: 'flex', alignItems: 'center', gap: '4px', marginTop: '2px' }}>
                    <Bot size={11} />
                    <span style={{ fontFamily: 'var(--font-mono)' }}>{node.agent_id || 'Sin asignar'}</span>
                  </div>
                )}

                {node.node_type === 'TASK' && (
                  <div style={{ fontSize: '11px', color: 'var(--accent)', display: 'flex', alignItems: 'center', gap: '4px', marginTop: '2px' }}>
                    <Terminal size={11} />
                    <span style={{ fontFamily: 'var(--font-mono)' }}>{node.tool_name || 'task'}</span>
                  </div>
                )}

                {node.node_type === 'WHILE' && (
                  <div style={{ fontSize: '11px', color: 'var(--accent)', display: 'flex', alignItems: 'center', gap: '4px', marginTop: '3px' }}>
                    <RotateCcw size={11} />
                    <span>Iteración máx: {node.control_config?.max_iterations ?? 3} ({node.control_config?.on_limit || 'ABORT'})</span>
                  </div>
                )}

                {(node.node_type === 'IF' || node.node_type === 'DECISION') && node.control_config?.condition && (
                  <div style={{ fontSize: '11px', color: 'var(--accent)', fontFamily: 'var(--font-mono)', marginTop: '3px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {typeof node.control_config.condition === 'string'
                      ? node.control_config.condition
                      : `${node.control_config.condition.field || ''} ${node.control_config.condition.operator || '=='} ${node.control_config.condition.expected_value ?? ''}`}
                  </div>
                )}

                {node.node_type === 'DELEGATE' && (
                  <div style={{ fontSize: '11px', color: 'var(--muted)', display: 'flex', alignItems: 'center', gap: '4px', marginTop: '3px' }}>
                    <Bot size={11} />
                    <span>Modo: {node.control_config?.routing_mode || node.control_config?.strategy || 'MANUAL'}</span>
                  </div>
                )}

                {node.node_type === 'HUMAN_APPROVAL' && node.status !== 'WAITING_APPROVAL' && (
                  <div style={{ fontSize: '11px', color: 'var(--muted)', marginTop: '3px', fontStyle: 'italic', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {node.control_config?.prompt || 'Pausa supervisada'}
                  </div>
                )}

                {/* Banner Interactivo si está en Espera de Aprobación */}
                {node.status === 'WAITING_APPROVAL' && (
                  <div
                    style={{
                      marginTop: '6px',
                      marginBottom: '6px',
                      padding: '6px',
                      backgroundColor: 'color-mix(in srgb, var(--muted) 15%, transparent)',
                      border: '1px solid color-mix(in srgb, var(--muted) 40%, transparent)',
                      borderRadius: '9px',
                    }}
                    onClick={(e) => e.stopPropagation()}
                  >
                    <div style={{ fontSize: '11px', color: 'var(--muted)', fontWeight: '600', marginBottom: '4px' }}>
                      {node.control_config?.prompt || 'Requiere aprobación'}
                    </div>
                    <div style={{ display: 'flex', gap: '4px' }}>
                      <button
                        onClick={() => handleApproveNode(nid, true)}
                        style={{
                          flex: 1,
                          padding: '3px 4px',
                          fontSize: '11px',
                          fontWeight: '700',
                          backgroundColor: 'var(--ok)',
                          color: 'var(--on-accent)',
                          border: 'none',
                          borderRadius: '6px',
                          cursor: 'pointer',
                        }}
                      >
                        Aprobar
                      </button>
                      <button
                        onClick={() => handleApproveNode(nid, false)}
                        style={{
                          flex: 1,
                          padding: '3px 4px',
                          fontSize: '11px',
                          fontWeight: '700',
                          backgroundColor: 'var(--bad)',
                          color: 'var(--on-accent)',
                          border: 'none',
                          borderRadius: '6px',
                          cursor: 'pointer',
                        }}
                      >
                        Rechazar
                      </button>
                    </div>
                  </div>
                )}

                {/* Acciones al pie del nodo */}
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    marginTop: '8px',
                    paddingTop: '6px',
                    borderTop: '1px solid color-mix(in srgb, var(--text) 7%, transparent)',
                  }}
                >
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      handleBacktrack(nid);
                    }}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '3px',
                      background: 'none',
                      border: 'none',
                      color: 'var(--accent)',
                      fontSize: '11px',
                      fontWeight: '600',
                      cursor: 'pointer',
                      padding: '1px 3px',
                    }}
                    title="Rebobinar estado a este punto de restauración"
                  >
                    <RotateCcw size={10} />
                    <span>Retroceder</span>
                  </button>

                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      handleDeleteNode(nid);
                    }}
                    style={{
                      background: 'none',
                      border: 'none',
                      color: 'var(--bad)',
                      cursor: 'pointer',
                      padding: '1px 3px',
                    }}
                    title="Eliminar nodo"
                  >
                    <Trash2 size={11} />
                  </button>
                </div>
              </div>
            );
          })}

          {nodesArray.length === 0 && (
            <div
              style={{
                position: 'absolute',
                top: '50%',
                left: '50%',
                transform: 'translate(-50%, -50%)',
                textAlign: 'center',
                color: 'var(--muted)',
              }}
            >
              <Workflow size={36} style={{ margin: '0 auto 10px', opacity: 0.4 }} />
              <div style={{ fontSize: '14.5px', fontWeight: '600', color: 'var(--text)' }}>
                El lienzo está vacío
              </div>
              <p style={{ fontSize: '12.5px', marginTop: '4px' }}>
                Elige un tipo de nodo en la paleta de la izquierda para empezar a componer el flujo de agentes.
              </p>
            </div>
          )}
          </div>
        </div>

        {/* Inspector Lateral Derecho (Propiedades de Nodo o Arista) */}
        <div
          style={{
            width: '310px',
            backgroundColor: 'var(--bg)',
            borderLeft: '1px solid var(--line)',
            display: 'flex',
            flexDirection: 'column',
            flexShrink: 0,
            zIndex: 10,
            overflowY: 'auto',
          }}
        >
          {nodeEditForm ? (
            /* Inspector de Nodo */
            <div style={{ padding: '16px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid var(--line)', paddingBottom: '8px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <Settings2 size={15} style={{ color: 'var(--accent)' }} />
                  <h3 style={{ fontSize: '13px', fontWeight: '700', color: 'var(--text)', margin: 0 }}>
                    Propiedades del nodo
                  </h3>
                </div>
                <button
                  onClick={() => setSelectedNodeId(null)}
                  style={{ background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer' }}
                >
                  <X size={14} />
                </button>
              </div>

              <form onSubmit={handleSaveNodeProperties} style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                <NodeFields
                  form={nodeEditForm}
                  setForm={setNodeEditForm}
                  nodes={nodesArray}
                  outgoingEdges={edgesArray.filter((e) => e.from_node === nodeEditForm.node_id)}
                  agents={registeredAgents}
                />

                <div style={{ display: 'flex', gap: '8px', marginTop: '6px' }}>
                  <button
                    type="submit"
                    style={{
                      flex: 1,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: '5px',
                      padding: '6px 10px',
                      backgroundColor: 'var(--ok)',
                      border: 'none',
                      borderRadius: '6px',
                      color: 'var(--on-accent)',
                      fontSize: '11px',
                      fontWeight: '600',
                      cursor: 'pointer',
                    }}
                  >
                    <Save size={12} />
                    <span>Guardar</span>
                  </button>

                  <button
                    type="button"
                    onClick={() => handleDeleteNode(nodeEditForm.node_id)}
                    style={{
                      padding: '6px 10px',
                      backgroundColor: 'color-mix(in srgb, var(--bad) 15%, transparent)',
                      border: '1px solid color-mix(in srgb, var(--bad) 40%, transparent)',
                      borderRadius: '6px',
                      color: 'var(--bad)',
                      fontSize: '11px',
                      cursor: 'pointer',
                    }}
                    title="Eliminar nodo del grafo"
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              </form>
            </div>
          ) : edgeEditForm ? (
            /* Inspector de Arista / Función de Unión */
            <div style={{ padding: '16px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid var(--line)', paddingBottom: '8px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <GitMerge size={15} style={{ color: 'var(--muted)' }} />
                  <h3 style={{ fontSize: '13px', fontWeight: '700', color: 'var(--text)', margin: 0 }}>
                    Unión entre nodos
                  </h3>
                </div>
                <button
                  onClick={() => setSelectedEdgeId(null)}
                  style={{ background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer' }}
                >
                  <X size={14} />
                </button>
              </div>

              <form onSubmit={handleSaveEdgeProperties} style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                <div>
                  <label style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)', display: 'block', marginBottom: '4px' }}>Conexión</label>
                  <div style={{ fontSize: '12.5px', color: 'var(--accent)', backgroundColor: 'var(--surface)', padding: '6px 8px', borderRadius: '6px' }}>
                    {nodesArray.find((n) => (n.node_id || n.id) === edgeEditForm.from_node)?.name || edgeEditForm.from_node}
                    {' '}&rarr;{' '}
                    {nodesArray.find((n) => (n.node_id || n.id) === edgeEditForm.to_node)?.name || edgeEditForm.to_node}
                  </div>
                </div>

                <div>
                  <label style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)', display: 'block', marginBottom: '4px' }}>Nombre del camino (opcional)</label>
                  <input
                    type="text"
                    placeholder="p. ej. Aprobado, Necesita cambios"
                    value={edgeEditForm.label}
                    onChange={(e) => setEdgeEditForm({ ...edgeEditForm, label: e.target.value })}
                    style={{ width: '100%', backgroundColor: 'var(--surface)', border: '1px solid var(--line)', borderRadius: '6px', padding: '5px 8px', fontSize: '12.5px', color: 'var(--text)' }}
                  />
                </div>

                <div style={{ backgroundColor: 'var(--surface)', border: '1px solid var(--line)', borderRadius: '9px', padding: '10px' }}>
                  <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', fontWeight: '600', color: 'var(--muted)', cursor: 'pointer', marginBottom: '8px' }}>
                    <input
                      type="checkbox"
                      checked={edgeEditForm.has_condition}
                      onChange={(e) => setEdgeEditForm({ ...edgeEditForm, has_condition: e.target.checked, cond: edgeEditForm.cond || emptyCondition() })}
                    />
                    <span>Seguir este camino solo si…</span>
                  </label>

                  {edgeEditForm.has_condition && (
                    <ConditionBuilder
                      value={edgeEditForm.cond}
                      onChange={(cond) => setEdgeEditForm({ ...edgeEditForm, cond })}
                      nodes={nodesArray}
                      excludeNodeId={edgeEditForm.to_node}
                      lastResultLabel="El nodo de origen de este camino"
                    />
                  )}
                </div>

                <div style={{ display: 'flex', gap: '8px', marginTop: '6px' }}>
                  <button
                    type="submit"
                    style={{
                      flex: 1,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: '5px',
                      padding: '6px 10px',
                      backgroundColor: 'var(--ok)',
                      border: 'none',
                      borderRadius: '6px',
                      color: 'var(--on-accent)',
                      fontSize: '11px',
                      fontWeight: '600',
                      cursor: 'pointer',
                    }}
                  >
                    <Save size={12} />
                    <span>Guardar la unión</span>
                  </button>

                  <button
                    type="button"
                    onClick={() => handleDeleteEdge(edgeEditForm.edge_id)}
                    style={{
                      padding: '6px 10px',
                      backgroundColor: 'color-mix(in srgb, var(--bad) 15%, transparent)',
                      border: '1px solid color-mix(in srgb, var(--bad) 40%, transparent)',
                      borderRadius: '6px',
                      color: 'var(--bad)',
                      fontSize: '11px',
                      cursor: 'pointer',
                    }}
                    title="Eliminar arista"
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              </form>
            </div>
          ) : (
            /* Resumen de Información del Flujo */
            <div style={{ padding: '16px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <div style={{ borderBottom: '1px solid var(--line)', paddingBottom: '8px' }}>
                <div style={{ fontSize: '11px', fontWeight: '700', textTransform: 'uppercase', color: 'var(--muted)' }}>
                  Flujo activo
                </div>
                <h3 style={{ fontSize: '13px', fontWeight: '700', color: 'var(--text)', margin: '4px 0' }}>
                  {workflow?.name || 'Selecciona un flujo'}
                </h3>
                <p style={{ fontSize: '11px', color: 'var(--text)', margin: 0 }}>
                  {workflow?.description || 'Sin descripción'}
                </p>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }}>
                <div style={{ backgroundColor: 'var(--surface)', padding: '8px', borderRadius: '6px', border: '1px solid var(--line)' }}>
                  <div style={{ fontSize: '11px', color: 'var(--muted)' }}>Nodos</div>
                  <div style={{ fontSize: '14.5px', fontWeight: '700', color: 'var(--accent)' }}>{nodesArray.length}</div>
                </div>
                <div style={{ backgroundColor: 'var(--surface)', padding: '8px', borderRadius: '6px', border: '1px solid var(--line)' }}>
                  <div style={{ fontSize: '11px', color: 'var(--muted)' }}>Conexiones</div>
                  <div style={{ fontSize: '14.5px', fontWeight: '700', color: 'var(--muted)' }}>{edgesArray.length}</div>
                </div>
              </div>

              <div style={{ backgroundColor: 'var(--surface)', padding: '10px', borderRadius: '9px', border: '1px solid var(--line)' }}>
                <div style={{ fontSize: '11px', fontWeight: '600', color: 'var(--accent)', marginBottom: '6px', display: 'flex', alignItems: 'center', gap: '4px' }}>
                  <Info size={12} />
                  <span>Instrucciones rápidas</span>
                </div>
                <ul style={{ fontSize: '11px', color: 'var(--text)', paddingLeft: '14px', margin: 0, display: 'flex', flexDirection: 'column', gap: '4px' }}>
                  <li><strong>Crear nodo:</strong> Haz clic en cualquier botón de la paleta izquierda.</li>
                  <li><strong>Editar propiedades:</strong> Haz clic en un nodo para asignarle agente, entradas y rol.</li>
                  <li><strong>Conectar nodos:</strong> Haz clic en el círculo derecho (salida) de un nodo y luego en el círculo izquierdo de otro.</li>
                  <li><strong>Configurar uniones:</strong> Haz clic en una conexión para editar su condición lógica.</li>
                  <li><strong>Mover nodos:</strong> Arrastra y suelta libremente sobre el lienzo.</li>
                </ul>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Barra Inferior de Telemetría y Logs */}
      <div
        style={{
          height: '65px',
          backgroundColor: 'var(--bg)',
          borderTop: '1px solid var(--line)',
          padding: '6px 14px',
          overflowY: 'auto',
          fontSize: '11px',
          fontFamily: 'var(--font-mono)',
          color: 'var(--text)',
          display: 'flex',
          flexDirection: 'column',
          gap: '2px',
          flexShrink: 0,
        }}
      >
        {logs.map((log, idx) => (
          <div key={idx} style={{ display: 'flex', gap: '8px', lineHeight: 1.3 }}>
            <span style={{ color: 'var(--muted)' }}>[{log.time}]</span>
            <span style={{ color: log.tag === 'ERROR' ? 'var(--bad)' : (log.tag === 'VALID' ? 'var(--ok)' : 'var(--accent)'), fontWeight: '600' }}>
              {log.tag}:
            </span>
            <span>{log.msg}</span>
          </div>
        ))}
      </div>

      {/* MODAL: Crear Nuevo Flujo */}
      {showCreateWfModal && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'var(--scrim)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
          }}
        >
          <div
            style={{
              backgroundColor: 'var(--surface)',
              border: '1px solid var(--line)',
              borderRadius: '9px',
              padding: '20px',
              width: '400px',
              display: 'flex',
              flexDirection: 'column',
              gap: '12px',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <h3 style={{ fontSize: '14.5px', fontWeight: '700', color: 'var(--text)', margin: 0 }}>
                Nuevo flujo de trabajo
              </h3>
              <button onClick={() => setShowCreateWfModal(false)} style={{ background: 'none', border: 'none', color: 'var(--text)', cursor: 'pointer' }}>
                <X size={16} />
              </button>
            </div>
            <form onSubmit={handleCreateWorkflow} style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              <div>
                <label style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)', display: 'block', marginBottom: '4px' }}>Nombre:</label>
                <input
                  type="text"
                  required
                  value={newWfName}
                  onChange={(e) => setNewWfName(e.target.value)}
                  placeholder="p. ej. Auditoría y corrección"
                  style={{ width: '100%', backgroundColor: 'var(--bg)', border: '1px solid var(--line)', borderRadius: '9px', padding: '6px 10px', fontSize: '12.5px', color: 'var(--text)' }}
                />
              </div>
              <div>
                <label style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)', display: 'block', marginBottom: '4px' }}>Descripción:</label>
                <input
                  type="text"
                  value={newWfDesc}
                  onChange={(e) => setNewWfDesc(e.target.value)}
                  placeholder="Descripción del objetivo del flujo"
                  style={{ width: '100%', backgroundColor: 'var(--bg)', border: '1px solid var(--line)', borderRadius: '9px', padding: '6px 10px', fontSize: '12.5px', color: 'var(--text)' }}
                />
              </div>
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '6px' }}>
                <button type="button" onClick={() => setShowCreateWfModal(false)} style={{ padding: '6px 12px', borderRadius: '6px', backgroundColor: 'var(--line)', border: 'none', color: 'var(--text)', fontSize: '12.5px', cursor: 'pointer' }}>
                  Cancelar
                </button>
                <button type="submit" style={{ padding: '6px 14px', borderRadius: '6px', backgroundColor: 'var(--ok)', border: 'none', color: 'var(--on-accent)', fontSize: '12.5px', fontWeight: '600', cursor: 'pointer' }}>
                  Crear flujo
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL: Añadir Nodo */}
      {showAddNodeModal && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'var(--scrim)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
          }}
        >
          <div
            style={{
              backgroundColor: 'var(--surface)',
              border: '1px solid var(--line)',
              borderRadius: '9px',
              padding: '20px',
              width: '420px',
              display: 'flex',
              flexDirection: 'column',
              gap: '12px',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <h3 style={{ fontSize: '14.5px', fontWeight: '700', color: 'var(--text)', margin: 0 }}>
                Añadir un nodo al flujo
              </h3>
              <button onClick={() => setShowAddNodeModal(false)} style={{ background: 'none', border: 'none', color: 'var(--text)', cursor: 'pointer' }}>
                <X size={16} />
              </button>
            </div>
            <form onSubmit={handleAddNode} style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              <div>
                <label style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)', display: 'block', marginBottom: '4px' }}>Nombre del nodo</label>
                <input
                  type="text"
                  required
                  value={createNodeForm.name}
                  onChange={(e) => setCreateNodeForm({ ...createNodeForm, name: e.target.value })}
                  placeholder="p. ej. Auditoría de seguridad"
                  style={{ width: '100%', backgroundColor: 'var(--bg)', border: '1px solid var(--line)', borderRadius: '9px', padding: '6px 10px', fontSize: '12.5px', color: 'var(--text)' }}
                />
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }}>
                <div>
                  <label style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)', display: 'block', marginBottom: '4px' }}>Qué hace este nodo</label>
                  <select
                    value={createNodeForm.node_type}
                    onChange={(e) => setCreateNodeForm({ ...createNodeForm, node_type: e.target.value })}
                    style={{ width: '100%', backgroundColor: 'var(--bg)', border: '1px solid var(--line)', borderRadius: '9px', padding: '6px 10px', fontSize: '12.5px', color: 'var(--text)' }}
                  >
                    {Object.entries(NODE_TYPE_LABELS)
                      .filter(([value]) => value !== 'START')
                      .map(([value, label]) => (
                        <option key={value} value={value}>
                          {label}
                        </option>
                      ))}
                  </select>
                </div>
                <div>
                  <label style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)', display: 'block', marginBottom: '4px' }}>Agente que lo ejecuta</label>
                  {WORK_TYPES.includes(createNodeForm.node_type) ? (
                    <select
                      value={createNodeForm.agent_id}
                      onChange={(e) => setCreateNodeForm({ ...createNodeForm, agent_id: e.target.value })}
                      style={{ width: '100%', backgroundColor: 'var(--bg)', border: '1px solid var(--line)', borderRadius: '9px', padding: '6px 10px', fontSize: '12.5px', color: 'var(--ok)' }}
                    >
                      <option value="">Elige un agente…</option>
                      {registeredAgents.map((ag) => (
                        <option key={ag.id} value={ag.id}>
                          {ag.name || ag.id}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      type="text"
                      disabled
                      placeholder="No aplica"
                      style={{ width: '100%', backgroundColor: 'var(--bg)', border: '1px solid var(--line)', borderRadius: '9px', padding: '6px 10px', fontSize: '12.5px', color: 'var(--muted)' }}
                    />
                  )}
                </div>
              </div>

              {createNodeForm.node_type === 'TASK' && (
                <div>
                  <label style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)', display: 'block', marginBottom: '4px' }}>Herramienta fija</label>
                  <select
                    value={createNodeForm.tool_name}
                    onChange={(e) => setCreateNodeForm({ ...createNodeForm, tool_name: e.target.value })}
                    style={{ width: '100%', backgroundColor: 'var(--bg)', border: '1px solid var(--line)', borderRadius: '9px', padding: '6px 10px', fontSize: '12.5px', color: 'var(--text)' }}
                  >
                    {TOOL_CHOICES.map((t) => (
                      <option key={t.value} value={t.value}>
                        {t.label}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              <div style={{ fontSize: '11px', color: 'var(--muted)' }}>
                La misión del nodo, el resultado esperado y las conexiones se configuran después en el panel derecho.
              </div>

              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '6px' }}>
                <button type="button" onClick={() => setShowAddNodeModal(false)} style={{ padding: '6px 12px', borderRadius: '6px', backgroundColor: 'var(--line)', border: 'none', color: 'var(--text)', fontSize: '12.5px', cursor: 'pointer' }}>
                  Cancelar
                </button>
                <button type="submit" style={{ padding: '6px 14px', borderRadius: '6px', backgroundColor: 'var(--ok)', border: 'none', color: 'var(--on-accent)', fontSize: '12.5px', fontWeight: '600', cursor: 'pointer' }}>
                  Añadir al lienzo
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL: Conectar Nodos / Crear Unión */}
      {showConnectModal && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'var(--scrim)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
          }}
        >
          <div
            style={{
              backgroundColor: 'var(--surface)',
              border: '1px solid var(--line)',
              borderRadius: '9px',
              padding: '20px',
              width: '450px',
              display: 'flex',
              flexDirection: 'column',
              gap: '12px',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <h3 style={{ fontSize: '14.5px', fontWeight: '700', color: 'var(--text)', margin: 0 }}>
                Conectar nodos
              </h3>
              <button onClick={() => setShowConnectModal(false)} style={{ background: 'none', border: 'none', color: 'var(--text)', cursor: 'pointer' }}>
                <X size={16} />
              </button>
            </div>
            <form onSubmit={handleConnectNodesModal} style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }}>
                <div>
                  <label style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)', display: 'block', marginBottom: '4px' }}>Nodo de origen</label>
                  <select
                    required
                    value={connectForm.from_node}
                    onChange={(e) => setConnectForm({ ...connectForm, from_node: e.target.value })}
                    style={{ width: '100%', backgroundColor: 'var(--bg)', border: '1px solid var(--line)', borderRadius: '9px', padding: '6px 10px', fontSize: '12.5px', color: 'var(--text)' }}
                  >
                    <option value="">Elegir…</option>
                    {nodesArray.map((n) => {
                      const nid = n.node_id || n.id;
                      return <option key={nid} value={nid}>{n.name} ({nid})</option>;
                    })}
                  </select>
                </div>
                <div>
                  <label style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)', display: 'block', marginBottom: '4px' }}>Nodo de destino</label>
                  <select
                    required
                    value={connectForm.to_node}
                    onChange={(e) => setConnectForm({ ...connectForm, to_node: e.target.value })}
                    style={{ width: '100%', backgroundColor: 'var(--bg)', border: '1px solid var(--line)', borderRadius: '9px', padding: '6px 10px', fontSize: '12.5px', color: 'var(--text)' }}
                  >
                    <option value="">Elegir…</option>
                    {nodesArray.map((n) => {
                      const nid = n.node_id || n.id;
                      return <option key={nid} value={nid}>{n.name} ({nid})</option>;
                    })}
                  </select>
                </div>
              </div>
              <div>
                <label style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)', display: 'block', marginBottom: '4px' }}>Etiqueta (opcional)</label>
                <input
                  type="text"
                  value={connectForm.label}
                  onChange={(e) => setConnectForm({ ...connectForm, label: e.target.value })}
                  placeholder="p. ej. Si status == SUCCESS"
                  style={{ width: '100%', backgroundColor: 'var(--bg)', border: '1px solid var(--line)', borderRadius: '9px', padding: '6px 10px', fontSize: '12.5px', color: 'var(--text)' }}
                />
              </div>

              {/* Condición lógica */}
              <div style={{ backgroundColor: 'var(--bg)', border: '1px solid var(--line)', borderRadius: '9px', padding: '10px' }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', fontWeight: '600', color: 'var(--muted)', cursor: 'pointer' }}>
                  <input
                    type="checkbox"
                    checked={connectForm.has_condition}
                    onChange={(e) => setConnectForm({ ...connectForm, has_condition: e.target.checked })}
                  />
                  <span>Aplicar una condición lógica a la unión (ramificación)</span>
                </label>

                {connectForm.has_condition && (
                  <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 0.8fr 1fr', gap: '6px', marginTop: '8px' }}>
                    <div>
                      <label style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)', display: 'block', marginBottom: '4px' }}>Campo</label>
                      <input
                        type="text"
                        value={connectForm.field}
                        onChange={(e) => setConnectForm({ ...connectForm, field: e.target.value })}
                        placeholder="output.status"
                        style={{ width: '100%', backgroundColor: 'var(--surface)', border: '1px solid var(--line)', borderRadius: '6px', padding: '4px 6px', fontSize: '11px', color: 'var(--text)' }}
                      />
                    </div>
                    <div>
                      <label style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)', display: 'block', marginBottom: '4px' }}>Operador</label>
                      <select
                        value={connectForm.operator}
                        onChange={(e) => setConnectForm({ ...connectForm, operator: e.target.value })}
                        style={{ width: '100%', backgroundColor: 'var(--surface)', border: '1px solid var(--line)', borderRadius: '6px', padding: '4px 6px', fontSize: '11px', color: 'var(--text)' }}
                      >
                        <option value="==">==</option>
                        <option value="!=">!=</option>
                        <option value=">">&gt;</option>
                        <option value=">=">&gt;=</option>
                        <option value="<">&lt;</option>
                        <option value="<=">&lt;=</option>
                        <option value="in">in</option>
                        <option value="contains">contains</option>
                        <option value="is_true">is_true</option>
                        <option value="is_false">is_false</option>
                      </select>
                    </div>
                    <div>
                      <label style={{ fontSize: 'var(--fs-secondary)', color: 'var(--muted)', display: 'block', marginBottom: '4px' }}>Valor</label>
                      <input
                        type="text"
                        value={connectForm.expected_value}
                        onChange={(e) => setConnectForm({ ...connectForm, expected_value: e.target.value })}
                        placeholder="SUCCESS"
                        style={{ width: '100%', backgroundColor: 'var(--surface)', border: '1px solid var(--line)', borderRadius: '6px', padding: '4px 6px', fontSize: '11px', color: 'var(--text)' }}
                      />
                    </div>
                  </div>
                )}
              </div>

              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '6px' }}>
                <button type="button" onClick={() => setShowConnectModal(false)} style={{ padding: '6px 12px', borderRadius: '6px', backgroundColor: 'var(--line)', border: 'none', color: 'var(--text)', fontSize: '12.5px', cursor: 'pointer' }}>
                  Cancelar
                </button>
                <button type="submit" style={{ padding: '6px 14px', borderRadius: '6px', backgroundColor: 'var(--ok)', border: 'none', color: 'var(--on-accent)', fontSize: '12.5px', fontWeight: '600', cursor: 'pointer' }}>
                  Conectar
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL: Plantillas Canónicas de Workflows */}
      {showTemplatesModal && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'var(--scrim)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
          }}
        >
          <div
            style={{
              backgroundColor: 'var(--surface)',
              border: '1px solid var(--line)',
              borderRadius: '11px',
              padding: '24px',
              width: '680px',
              maxWidth: '92vw',
              maxHeight: '85vh',
              display: 'flex',
              flexDirection: 'column',
              gap: '16px',
              boxShadow: 'none',
              overflowY: 'auto',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid var(--line)', paddingBottom: '12px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <div style={{ width: '28px', height: '28px', borderRadius: '9px', backgroundColor: 'color-mix(in srgb, var(--accent) 15%, transparent)', color: 'var(--accent)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <LayoutTemplate size={16} />
                </div>
                <div>
                  <h3 style={{ fontSize: '14.5px', fontWeight: '700', color: 'var(--text)', margin: 0 }}>
                    Plantillas canónicas de orquestación
                  </h3>
                  <p style={{ fontSize: '11px', color: 'var(--text)', margin: 0 }}>
                    Flujos multiagente preconfigurados con la semántica de PRAXEON
                  </p>
                </div>
              </div>
              <button
                onClick={() => setShowTemplatesModal(false)}
                style={{ background: 'none', border: 'none', color: 'var(--text)', cursor: 'pointer', padding: '4px' }}
              >
                <X size={18} />
              </button>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              {canonicalTemplates.length > 0 ? (
                canonicalTemplates.map((tpl) => (
                  <div
                    key={tpl.template_id || tpl.key}
                    style={{
                      backgroundColor: 'var(--line)',
                      border: '1px solid var(--line)',
                      borderRadius: '9px',
                      padding: '14px 16px',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '8px',
                      transition: 'border-color 0.2s ease',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span style={{ fontSize: '13px', fontWeight: '700', color: 'var(--text)' }}>
                          {tpl.name}
                        </span>
                        <span
                          style={{
                            fontSize: '11px',
                            fontWeight: '600',
                            padding: '2px 6px',
                            borderRadius: '6px',
                            backgroundColor: 'color-mix(in srgb, var(--accent) 15%, transparent)',
                            color: 'var(--accent)',
                            fontFamily: 'var(--font-mono)',
                          }}
                        >
                          {tpl.template_id || tpl.key}
                        </span>
                      </div>
                      <div style={{ fontSize: '11px', color: 'var(--muted)' }}>
                        {tpl.node_count} nodos &bull; {tpl.edge_count} aristas
                      </div>
                    </div>

                    <p style={{ fontSize: '12.5px', color: 'var(--text)', margin: 0, lineHeight: 1.4 }}>
                      {tpl.description}
                    </p>

                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: '4px', paddingTop: '8px', borderTop: '1px solid color-mix(in srgb, var(--on-accent) 6%, transparent)' }}>
                      <div style={{ display: 'flex', gap: '6px' }}>
                        {(tpl.template_id || tpl.key) === 'code_review_loop' && (
                          <span style={{ fontSize: '11px', color: 'var(--accent)', backgroundColor: 'color-mix(in srgb, var(--accent) 12%, transparent)', padding: '2px 6px', borderRadius: '6px' }}>
                            Bucle Acotado (WHILE &le; 3)
                          </span>
                        )}
                        {(tpl.template_id || tpl.key) === 'research_writer_reviewer' && (
                          <span style={{ fontSize: '11px', color: 'var(--muted)', backgroundColor: 'color-mix(in srgb, var(--muted) 12%, transparent)', padding: '2px 6px', borderRadius: '6px' }}>
                            Compuerta humana (aprobación)
                          </span>
                        )}
                        {(tpl.template_id || tpl.key) === 'triage_router' && (
                          <span style={{ fontSize: '11px', color: 'var(--muted)', backgroundColor: 'color-mix(in srgb, var(--muted) 12%, transparent)', padding: '2px 6px', borderRadius: '6px' }}>
                            Enrutador IF + unión paralela
                          </span>
                        )}
                      </div>

                      <button
                        onClick={() => handleInstantiateTemplate(tpl.template_id || tpl.key, tpl.name)}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: '6px',
                          padding: '6px 12px',
                          borderRadius: '9px',
                          backgroundColor: 'var(--accent)',
                          border: 'none',
                          color: 'var(--on-accent)',
                          fontSize: '11px',
                          fontWeight: '600',
                          cursor: 'pointer',
                        }}
                      >
                        <Plus size={12} />
                        <span>Usar plantilla</span>
                      </button>
                    </div>
                  </div>
                ))
              ) : (
                <div style={{ textAlign: 'center', padding: '30px', color: 'var(--muted)' }}>
                  <LayoutTemplate size={32} style={{ margin: '0 auto 8px', opacity: 0.5 }} />
                  <div>No se han cargado las plantillas o el servidor no está disponible.</div>
                  <button
                    onClick={() => loadTemplates()}
                    style={{
                      marginTop: '10px',
                      padding: '5px 12px',
                      borderRadius: '6px',
                      backgroundColor: 'var(--line)',
                      border: '1px solid var(--line)',
                      color: 'var(--text)',
                      fontSize: '11px',
                      cursor: 'pointer',
                    }}
                  >
                    Reintentar
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

