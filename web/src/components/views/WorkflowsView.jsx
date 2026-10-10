import React, { useState, useEffect, useRef, useMemo } from 'react';
import {
  Workflow,
  ExternalLink,
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
import {
  fetchWorkflows,
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
  getApiKey,
  fetchAgents,
} from '../../services/api';

export default function WorkflowsView({ session: _session = {}, events: _events = [] }) {
  const [workflows, setWorkflows] = useState([]);
  const [activeWorkflowId, setActiveWorkflowId] = useState(null);
  const [workflow, setWorkflow] = useState(null);
  const [_loading, setLoading] = useState(true);
  const [connectionError, setConnectionError] = useState(null);
  const [executing, setExecuting] = useState(false);
  const [validationResult, setValidationResult] = useState(null);
  const [viewMode, setViewMode] = useState('native'); // 'native' | 'iframe'
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
        setRegisteredAgents(Array.isArray(res.data) ? res.data : Object.values(res.data));
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
      alert(`Error al instanciar plantilla: ${err.message}`);
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

  // Nodos normalizados como Array siempre
  const nodesArray = useMemo(() => {
    if (!workflow || !workflow.nodes) return [];
    if (Array.isArray(workflow.nodes)) return workflow.nodes;
    return Object.values(workflow.nodes);
  }, [workflow]);

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
      const cfg = node.control_config || {};
      const cond = cfg.condition || {};
      setNodeEditForm({
        node_id: node.node_id || node.id,
        name: node.name || '',
        node_type: node.node_type || 'TASK',
        agent_id: node.agent_id || '',
        tool_name: node.tool_name || '',
        inputs_json: JSON.stringify(node.inputs || {}, null, 2),
        status: node.status || 'PENDING',
        outputs_json: JSON.stringify(node.outputs || {}, null, 2),
        // Campos de Semántica de Control
        max_iterations: cfg.max_iterations ?? 3,
        on_limit: cfg.on_limit || 'ABORT',
        prompt: cfg.prompt || 'Se requiere aprobación de un supervisor humano.',
        timeout_seconds: cfg.timeout_seconds || 3600,
        strategy: cfg.strategy || 'MANUAL',
        candidate_agents: Array.isArray(cfg.candidate_agents) ? cfg.candidate_agents.join(', ') : '',
        join_policy: cfg.join_policy || 'all',
        merge_policy: cfg.merge_policy || 'namespace',
        condition_field: cond.field || '',
        condition_operator: cond.operator || '==',
        condition_expected_value: cond.expected_value !== undefined ? (typeof cond.expected_value === 'object' ? JSON.stringify(cond.expected_value) : String(cond.expected_value)) : '',
      });
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
      const cond = edge.condition || {};
      setEdgeEditForm({
        edge_id: edge.edge_id,
        from_node: edge.from_node,
        to_node: edge.to_node,
        label: edge.label || '',
        has_condition: Boolean(edge.condition),
        field: cond.field || 'output.status',
        operator: cond.operator || '==',
        expected_value: cond.expected_value !== undefined ? (typeof cond.expected_value === 'object' ? JSON.stringify(cond.expected_value) : String(cond.expected_value)) : 'SUCCESS',
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
      alert(`Error al crear flujo: ${err.message}`);
    }
  };

  // Manejador para añadir un nodo nuevo
  const handleAddNode = async (e) => {
    e.preventDefault();
    if (!activeWorkflowId) {
      alert('No hay un workflow seleccionado. Selecciona o crea un workflow primero, y comprueba que el backend esté ejecutándose.');
      return;
    }
    if (!createNodeForm.name.trim()) {
      alert('Introduce un nombre para el nuevo nodo.');
      return;
    }
    try {
      const payload = {
        name: createNodeForm.name.trim(),
        node_type: createNodeForm.node_type,
        x: Number(createNodeForm.x) || 300,
        y: Number(createNodeForm.y) || 200,
        agent_id: createNodeForm.node_type === 'AGENT' ? (createNodeForm.agent_id.trim() || null) : null,
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
      alert(`Error al añadir nodo: ${err.message}`);
      addLog('ERROR', `Fallo al añadir nodo: ${err.message}`);
    }
  };

  // Crear nodo rápido desde la paleta de componentes
  const handleQuickAddNode = (type, defaultName) => {
    if (!activeWorkflowId) {
      alert('No hay ningún workflow seleccionado o activo. Comprueba que el servidor backend (puerto 8000) esté en ejecución y crea o selecciona un workflow.');
      return;
    }
    setCreateNodeForm({
      name: defaultName,
      node_type: type,
      agent_id: type === 'AGENT' && registeredAgents.length > 0 ? registeredAgents[0].id : '',
      tool_name: type === 'TASK' ? 'execute_command' : '',
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
      let parsedInputs = {};
      try {
        parsedInputs = nodeEditForm.inputs_json.trim() ? JSON.parse(nodeEditForm.inputs_json) : {};
      } catch (jsonErr) {
        alert(`Error en sintaxis JSON de Inputs: ${jsonErr.message}`);
        return;
      }

      // Ensamblar control_config según el tipo de nodo
      let controlConfig = null;
      const nt = nodeEditForm.node_type;

      if (nt === 'WHILE') {
        let cond = null;
        if (nodeEditForm.condition_field?.trim()) {
          let expVal = nodeEditForm.condition_expected_value;
          if (expVal === 'true') expVal = true;
          else if (expVal === 'false') expVal = false;
          else if (!isNaN(Number(expVal)) && expVal.trim() !== '') expVal = Number(expVal);
          cond = {
            field: nodeEditForm.condition_field.trim(),
            operator: nodeEditForm.condition_operator || '==',
            expected_value: expVal,
          };
        }
        controlConfig = {
          max_iterations: Number(nodeEditForm.max_iterations) || 3,
          on_limit: nodeEditForm.on_limit || 'ABORT',
          ...(cond ? { condition: cond } : {}),
        };
      } else if (nt === 'IF' || nt === 'DECISION') {
        let cond = null;
        if (nodeEditForm.condition_field?.trim()) {
          let expVal = nodeEditForm.condition_expected_value;
          if (expVal === 'true') expVal = true;
          else if (expVal === 'false') expVal = false;
          else if (!isNaN(Number(expVal)) && expVal.trim() !== '') expVal = Number(expVal);
          cond = {
            field: nodeEditForm.condition_field.trim(),
            operator: nodeEditForm.condition_operator || '==',
            expected_value: expVal,
          };
        }
        controlConfig = cond ? { condition: cond } : {};
      } else if (nt === 'HUMAN_APPROVAL') {
        controlConfig = {
          prompt: nodeEditForm.prompt?.trim() || 'Aprobación requerida para continuar.',
          timeout_seconds: Number(nodeEditForm.timeout_seconds) || 3600,
        };
      } else if (nt === 'DELEGATE') {
        const candidates = (nodeEditForm.candidate_agents || '')
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean);
        controlConfig = {
          strategy: nodeEditForm.strategy || 'MANUAL',
          candidate_agents: candidates,
        };
      } else if (nt === 'PARALLEL_JOIN') {
        controlConfig = {
          join_policy: nodeEditForm.join_policy || 'all',
          merge_policy: nodeEditForm.merge_policy || 'namespace',
        };
      }

      await updateWorkflowNode(activeWorkflowId, nodeEditForm.node_id, {
        name: nodeEditForm.name.trim(),
        node_type: nodeEditForm.node_type,
        agent_id: nodeEditForm.node_type === 'AGENT' ? (nodeEditForm.agent_id.trim() || null) : null,
        tool_name: nodeEditForm.node_type === 'TASK' ? (nodeEditForm.tool_name.trim() || null) : null,
        inputs: parsedInputs,
        ...(controlConfig ? { control_config: controlConfig } : {}),
      });

      addLog('NODE', `Propiedades de nodo '${nodeEditForm.name}' actualizadas.`);
      await loadActiveWorkflow(activeWorkflowId);
    } catch (err) {
      alert(`Error al actualizar nodo: ${err.message}`);
    }
  };

  // Guardar cambios de la arista / condición de unión seleccionada
  const handleSaveEdgeProperties = async (e) => {
    e.preventDefault();
    if (!activeWorkflowId || !edgeEditForm) return;
    try {
      let condition = null;
      if (edgeEditForm.has_condition) {
        let expectedVal = edgeEditForm.expected_value;
        if (edgeEditForm.operator === 'is_true' || edgeEditForm.operator === 'is_false') {
          expectedVal = null;
        } else if (expectedVal === 'true') {
          expectedVal = true;
        } else if (expectedVal === 'false') {
          expectedVal = false;
        } else if (!isNaN(Number(expectedVal)) && expectedVal.trim() !== '') {
          expectedVal = Number(expectedVal);
        } else {
          try {
            expectedVal = JSON.parse(expectedVal);
          } catch {
            // Dejar como string
          }
        }

        condition = {
          field: edgeEditForm.field.trim() || 'output.status',
          operator: edgeEditForm.operator,
          expected_value: expectedVal,
        };
      }

      await updateWorkflowEdge(activeWorkflowId, edgeEditForm.edge_id, {
        label: edgeEditForm.label.trim(),
        condition: condition,
      });

      addLog('EDGE', `Conexión '${edgeEditForm.edge_id}' actualizada (Condición: ${condition ? `${condition.field} ${condition.operator}` : 'Ninguna'}).`);
      await loadActiveWorkflow(activeWorkflowId);
    } catch (err) {
      alert(`Error al actualizar unión: ${err.message}`);
    }
  };

  // Eliminar nodo seleccionado
  const handleDeleteNode = async (nodeId) => {
    if (!activeWorkflowId) return;
    const targetNode = nodesArray.find((n) => (n.node_id || n.id) === nodeId);
    const nodeName = targetNode?.name || nodeId;
    if (!window.confirm(`¿Confirmas eliminar el nodo '${nodeName}' y todas sus conexiones?`)) return;
    try {
      await deleteWorkflowNode(activeWorkflowId, nodeId);
      if (selectedNodeId === nodeId) setSelectedNodeId(null);
      if (wiringSourceId === nodeId) setWiringSourceId(null);
      addLog('NODE', `Nodo '${nodeName}' eliminado.`);
      await loadActiveWorkflow(activeWorkflowId);
    } catch (err) {
      alert(`Error al eliminar nodo: ${err.message}`);
    }
  };

  // Eliminar arista seleccionada
  const handleDeleteEdge = async (edgeId) => {
    if (!activeWorkflowId || !window.confirm(`¿Eliminar esta conexión?`)) return;
    try {
      await deleteWorkflowEdge(activeWorkflowId, edgeId);
      if (selectedEdgeId === edgeId) setSelectedEdgeId(null);
      addLog('EDGE', `Conexión '${edgeId}' eliminada.`);
      await loadActiveWorkflow(activeWorkflowId);
    } catch (err) {
      alert(`Error al eliminar arista: ${err.message}`);
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
      alert(`Error al conectar nodos: ${err.message}`);
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
      alert(`Error al conectar nodos: ${err.message}`);
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
      alert(`Error en ejecución: ${err.message}`);
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
      alert(`Error al avanzar paso: ${err.message}`);
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
      alert(`Error en retroceso determinista: ${err.message}`);
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
      alert(`Error al reiniciar workflow: ${err.message}`);
    }
  };

  const handleApproveNode = async (nodeId, approved) => {
    if (!activeWorkflowId) return;
    try {
      await approveWorkflowNode(
        activeWorkflowId,
        nodeId,
        approved,
        approved ? 'Aprobado desde supervisor visual' : 'Rechazado desde supervisor visual'
      );
      addLog('APPROVAL', `Nodo '${nodeId}' ${approved ? 'APROBADO' : 'RECHAZADO'}.`);
      await loadActiveWorkflow(activeWorkflowId);
    } catch (err) {
      alert(`Error al procesar aprobación: ${err.message}`);
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
    switch (type) {
      case 'AGENT':
        return { color: '#10b981', bg: 'rgba(16, 185, 129, 0.12)', border: '#10b981', icon: <Bot size={13} />, label: 'Agente IA' };
      case 'TASK':
        return { color: '#38bdf8', bg: 'rgba(56, 189, 248, 0.12)', border: '#38bdf8', icon: <Terminal size={13} />, label: 'Tarea / Tool' };
      case 'DECISION':
      case 'IF':
        return { color: '#f59e0b', bg: 'rgba(245, 158, 11, 0.12)', border: '#f59e0b', icon: <GitBranch size={13} />, label: 'Decisión / IF' };
      case 'WHILE':
        return { color: '#06b6d4', bg: 'rgba(6, 182, 212, 0.12)', border: '#06b6d4', icon: <RotateCcw size={13} />, label: 'Bucle WHILE' };
      case 'DELEGATE':
        return { color: '#a855f7', bg: 'rgba(168, 85, 247, 0.12)', border: '#a855f7', icon: <Bot size={13} />, label: 'Delegar / Router' };
      case 'HUMAN_APPROVAL':
        return { color: '#ec4899', bg: 'rgba(236, 72, 153, 0.12)', border: '#ec4899', icon: <ShieldCheck size={13} />, label: 'Aprobación Humana' };
      case 'PARALLEL_FORK':
        return { color: '#c084fc', bg: 'rgba(192, 132, 252, 0.12)', border: '#c084fc', icon: <GitFork size={13} />, label: 'Fork Paralelo' };
      case 'PARALLEL_JOIN':
        return { color: '#818cf8', bg: 'rgba(129, 140, 248, 0.12)', border: '#818cf8', icon: <GitMerge size={13} />, label: 'Join / Unión' };
      case 'START':
        return { color: '#2dd4bf', bg: 'rgba(45, 212, 191, 0.12)', border: '#2dd4bf', icon: <Play size={13} />, label: 'Inicio' };
      case 'END':
        return { color: '#f43f5e', bg: 'rgba(244, 63, 94, 0.12)', border: '#f43f5e', icon: <Flag size={13} />, label: 'Fin' };
      default:
        return { color: '#94a3b8', bg: 'rgba(148, 163, 184, 0.12)', border: '#475569', icon: <Cpu size={13} />, label: type };
    }
  };

  const getNodeStatusBadge = (status) => {
    switch (status) {
      case 'COMPLETED':
        return { color: '#3fb950', bg: 'rgba(63, 185, 80, 0.2)' };
      case 'RUNNING':
        return { color: '#38bdf8', bg: 'rgba(56, 189, 248, 0.2)' };
      case 'FAILED':
        return { color: '#f85149', bg: 'rgba(248, 81, 73, 0.2)' };
      case 'SKIPPED':
        return { color: '#8b949e', bg: 'rgba(139, 148, 158, 0.2)' };
      case 'READY':
        return { color: '#e3b341', bg: 'rgba(227, 179, 65, 0.2)' };
      case 'WAITING_APPROVAL':
        return { color: '#ec4899', bg: 'rgba(236, 72, 153, 0.2)' };
      case 'WAITING_RESULT':
        return { color: '#a855f7', bg: 'rgba(168, 85, 247, 0.2)' };
      case 'RETRYING':
        return { color: '#f59e0b', bg: 'rgba(245, 158, 11, 0.2)' };
      default:
        return { color: '#64748b', bg: 'rgba(100, 116, 139, 0.2)' };
    }
  };


  const apiKey = getApiKey();
  const iframeUrl = apiKey
    ? `/v1/workflows/editor/ui?workflow_id=${encodeURIComponent(activeWorkflowId || '')}&token=${encodeURIComponent(apiKey)}`
    : `/v1/workflows/editor/ui?workflow_id=${encodeURIComponent(activeWorkflowId || '')}`;

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        flex: 1,
        height: '100%',
        backgroundColor: '#080c14',
        color: '#f8fafc',
        overflow: 'hidden',
        position: 'relative',
      }}
    >
      {/* Barra de Herramientas Superior */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '8px 16px',
          backgroundColor: '#0c1017',
          borderBottom: '1px solid #1e293b',
          flexShrink: 0,
          gap: '12px',
          flexWrap: 'wrap',
          zIndex: 40,
        }}
      >
        {/* Izquierda: Selector de Flujo y Estado */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: '28px',
              height: '28px',
              borderRadius: '6px',
              background: 'linear-gradient(135deg, rgba(56, 189, 248, 0.25), rgba(129, 140, 248, 0.25))',
              border: '1px solid rgba(56, 189, 248, 0.4)',
              color: '#38bdf8',
            }}
          >
            <Workflow size={16} />
          </div>

          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span style={{ fontSize: '13px', fontWeight: '700', letterSpacing: '-0.01em', color: '#f8fafc' }}>
                PRAXEON Workflows
              </span>
              <span
                style={{
                  fontSize: '9.5px',
                  fontWeight: '700',
                  padding: '1px 6px',
                  borderRadius: '10px',
                  backgroundColor: 'rgba(56, 189, 248, 0.15)',
                  color: '#38bdf8',
                  border: '1px solid rgba(56, 189, 248, 0.3)',
                }}
              >
                Visual DAG Orchestrator
              </span>
            </div>
          </div>

          {/* Selector de flujos */}
          <select
            value={activeWorkflowId || ''}
            onChange={(e) => setActiveWorkflowId(e.target.value)}
            style={{
              backgroundColor: '#161c28',
              border: '1px solid #243044',
              borderRadius: '6px',
              padding: '4px 8px',
              fontSize: '11.5px',
              color: '#f8fafc',
              cursor: 'pointer',
            }}
          >
            {workflows.map((wf) => (
              <option key={wf.workflow_id} value={wf.workflow_id}>
                {wf.name} ({wf.node_count ?? Object.keys(wf.nodes || {}).length} nodos)
              </option>
            ))}
          </select>

          <button
            onClick={() => setShowCreateWfModal(true)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
              padding: '4px 8px',
              borderRadius: '5px',
              backgroundColor: '#162234',
              border: '1px solid #2a3c5a',
              color: '#38bdf8',
              fontSize: '11px',
              cursor: 'pointer',
              fontWeight: '500',
            }}
            title="Crear un nuevo flujo de trabajo"
          >
            <Plus size={12} />
            <span>Nuevo Flujo</span>
          </button>

          <button
            onClick={() => {
              loadTemplates();
              setShowTemplatesModal(true);
            }}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
              padding: '4px 8px',
              borderRadius: '5px',
              backgroundColor: '#1b1b3a',
              border: '1px solid #4338ca',
              color: '#a5b4fc',
              fontSize: '11px',
              cursor: 'pointer',
              fontWeight: '500',
            }}
            title="Cargar flujos estructurados canónicos (Code Review, Writer-Reviewer, Triage Router)"
          >
            <LayoutTemplate size={12} />
            <span>Plantillas Canónicas</span>
          </button>
        </div>

        {/* Centro: Controles de Ejecución */}
        {viewMode === 'native' && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <button
              onClick={handleValidate}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
                padding: '4px 9px',
                borderRadius: '5px',
                backgroundColor: '#161c28',
                border: '1px solid #243044',
                color: '#cbd5e1',
                fontSize: '11px',
                cursor: 'pointer',
              }}
              title="Validar causalidad y ausencia de ciclos"
            >
              <ShieldCheck size={12} />
              <span>Validar DAG</span>
            </button>

            <button
              onClick={handleStep}
              disabled={executing}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
                padding: '4px 9px',
                borderRadius: '5px',
                backgroundColor: '#162234',
                border: '1px solid #2a3c5a',
                color: '#38bdf8',
                fontSize: '11px',
                cursor: 'pointer',
              }}
              title="Avanzar un nodo paso a paso"
            >
              <SkipForward size={12} />
              <span>Step</span>
            </button>

            <button
              onClick={handleExecute}
              disabled={executing}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
                padding: '4px 11px',
                borderRadius: '5px',
                backgroundColor: '#238636',
                border: 'none',
                color: '#ffffff',
                fontSize: '11px',
                fontWeight: '600',
                cursor: 'pointer',
              }}
              title="Ejecutar flujo completo supervisado"
            >
              <Play size={12} className={executing ? 'animate-pulse' : ''} />
              <span>{executing ? 'Ejecutando...' : 'Run Flow'}</span>
            </button>

            <button
              onClick={handleReset}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
                padding: '4px 9px',
                borderRadius: '5px',
                backgroundColor: '#161c28',
                border: '1px solid #243044',
                color: '#94a3b8',
                fontSize: '11px',
                cursor: 'pointer',
              }}
              title="Reiniciar ejecución del flujo"
            >
              <RotateCcw size={12} />
              <span>Reset</span>
            </button>

            <button
              onClick={handleCenterView}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
                padding: '4px 9px',
                borderRadius: '5px',
                backgroundColor: '#161c28',
                border: '1px solid #243044',
                color: '#94a3b8',
                fontSize: '11px',
                cursor: 'pointer',
              }}
              title="Centrar vista sobre los nodos del workflow"
            >
              <Focus size={12} />
              <span>Centrar</span>
            </button>
          </div>
        )}

        {/* Derecha: Selector de Modo (Nativo vs Standalone HTML) */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <div
            style={{
              display: 'flex',
              backgroundColor: '#121824',
              borderRadius: '5px',
              border: '1px solid #243044',
              padding: '2px',
            }}
          >
            <button
              onClick={() => setViewMode('native')}
              style={{
                padding: '3px 8px',
                borderRadius: '4px',
                fontSize: '10.5px',
                fontWeight: viewMode === 'native' ? '700' : '500',
                backgroundColor: viewMode === 'native' ? '#1e293b' : 'transparent',
                color: viewMode === 'native' ? '#38bdf8' : '#73849c',
                border: 'none',
                cursor: 'pointer',
              }}
            >
              Editor Visual React
            </button>
            <button
              onClick={() => setViewMode('iframe')}
              style={{
                padding: '3px 8px',
                borderRadius: '4px',
                fontSize: '10.5px',
                fontWeight: viewMode === 'iframe' ? '700' : '500',
                backgroundColor: viewMode === 'iframe' ? '#1e293b' : 'transparent',
                color: viewMode === 'iframe' ? '#38bdf8' : '#73849c',
                border: 'none',
                cursor: 'pointer',
              }}
            >
              Lienzo Standalone
            </button>
          </div>

          <button
            onClick={() => window.open(iframeUrl, '_blank', 'noopener,noreferrer')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
              padding: '4px 8px',
              borderRadius: '5px',
              backgroundColor: '#161c28',
              border: '1px solid #243044',
              color: '#94a3b8',
              fontSize: '11px',
              cursor: 'pointer',
            }}
            title="Abrir editor standalone en pestaña completa"
          >
            <ExternalLink size={12} />
          </button>
        </div>
      </div>

      {/* Banner de Desconexión de Backend */}
      {connectionError && (
        <div
          style={{
            backgroundColor: 'rgba(239, 68, 68, 0.15)',
            borderBottom: '1px solid rgba(239, 68, 68, 0.4)',
            padding: '8px 16px',
            fontSize: '11.5px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            color: '#fca5a5',
            flexShrink: 0,
            zIndex: 35,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <AlertTriangle size={14} color="#ef4444" />
            <span>
              <strong>Backend Desconectado ({connectionError}):</strong> Asegúrate de que el servidor PRAXEON esté ejecutándose en el puerto 8000 (<code>uv run praxeon-server --port 8000</code>).
            </span>
          </div>
          <button
            onClick={() => loadWorkflows()}
            style={{
              padding: '3px 8px',
              borderRadius: '4px',
              backgroundColor: '#b91c1c',
              border: '1px solid #ef4444',
              color: '#ffffff',
              fontSize: '10.5px',
              cursor: 'pointer',
              fontWeight: '600',
            }}
          >
            Reintentar Conexión
          </button>
        </div>
      )}

      {/* Banner de Validación */}
      {validationResult && (
        <div
          style={{
            backgroundColor: validationResult.is_valid ? 'rgba(63, 185, 80, 0.12)' : 'rgba(248, 81, 73, 0.12)',
            borderBottom: `1px solid ${validationResult.is_valid ? 'rgba(63, 185, 80, 0.35)' : 'rgba(248, 81, 73, 0.35)'}`,
            padding: '6px 16px',
            fontSize: '11px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            color: validationResult.is_valid ? '#3fb950' : '#f85149',
            flexShrink: 0,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            {validationResult.is_valid ? <CheckCircle2 size={13} /> : <AlertTriangle size={13} />}
            <span>
              {validationResult.is_valid
                ? `Grafo DAG Válido sin ciclos. Orden topológico: [${(validationResult.topological_order || []).join(' → ')}]`
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
            backgroundColor: 'rgba(56, 189, 248, 0.15)',
            borderBottom: '1px solid rgba(56, 189, 248, 0.4)',
            padding: '6px 16px',
            fontSize: '11.5px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            color: '#38bdf8',
            flexShrink: 0,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <ArrowRight size={14} className="animate-pulse" />
            <span>
              <strong>Modo Cableado:</strong> Origen fijado en nodo <strong>{wiringSourceId}</strong>. Haz clic en el puerto de entrada (izquierda) de otro nodo para crear la unión.
            </span>
          </div>
          <button
            onClick={() => setWiringSourceId(null)}
            style={{
              backgroundColor: '#1e293b',
              border: '1px solid #334155',
              color: '#f8fafc',
              padding: '2px 8px',
              borderRadius: '4px',
              fontSize: '10.5px',
              cursor: 'pointer',
            }}
          >
            Cancelar Cableado
          </button>
        </div>
      )}

      {/* Contenedor Principal */}
      {viewMode === 'iframe' ? (
        <div style={{ flex: 1, width: '100%', height: '100%', position: 'relative' }}>
          <iframe
            src={iframeUrl}
            title="PRAXEON Visual Workflow Orchestrator"
            style={{
              width: '100%',
              height: '100%',
              border: 'none',
              display: 'block',
              backgroundColor: '#080c14',
            }}
          />
        </div>
      ) : (
        <div style={{ display: 'flex', flex: 1, overflow: 'hidden', position: 'relative' }}>
          {/* Paleta Lateral Izquierda de Nodos */}
          <div
            style={{
              width: '175px',
              backgroundColor: '#0c1017',
              borderRight: '1px solid #1e293b',
              display: 'flex',
              flexDirection: 'column',
              padding: '12px 10px',
              gap: '8px',
              flexShrink: 0,
              zIndex: 10,
              overflowY: 'auto',
            }}
          >
            <div style={{ fontSize: '10px', fontWeight: '700', textTransform: 'uppercase', color: '#64748b', letterSpacing: '0.05em' }}>
              Paleta de Nodos
            </div>

            <button
              onClick={() => handleQuickAddNode('AGENT', 'Agent Worker')}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                padding: '7px 8px',
                backgroundColor: '#121824',
                border: '1px solid #233147',
                borderRadius: '6px',
                color: '#f8fafc',
                fontSize: '11px',
                cursor: 'pointer',
                textAlign: 'left',
              }}
              title="Añadir nodo de agente autónomo"
            >
              <div style={{ width: '20px', height: '20px', borderRadius: '4px', backgroundColor: 'rgba(16, 185, 129, 0.2)', color: '#10b981', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Bot size={12} />
              </div>
              <div>
                <div style={{ fontWeight: '600' }}>Agente</div>
                <div style={{ fontSize: '9px', color: '#64748b' }}>IA delegada</div>
              </div>
            </button>

            <button
              onClick={() => handleQuickAddNode('TASK', 'Task Worker')}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                padding: '7px 8px',
                backgroundColor: '#121824',
                border: '1px solid #233147',
                borderRadius: '6px',
                color: '#f8fafc',
                fontSize: '11px',
                cursor: 'pointer',
                textAlign: 'left',
              }}
              title="Añadir tarea o herramienta de ejecución"
            >
              <div style={{ width: '20px', height: '20px', borderRadius: '4px', backgroundColor: 'rgba(56, 189, 248, 0.2)', color: '#38bdf8', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Terminal size={12} />
              </div>
              <div>
                <div style={{ fontWeight: '600' }}>Tarea</div>
                <div style={{ fontSize: '9px', color: '#64748b' }}>Tool / Comando</div>
              </div>
            </button>

            <button
              onClick={() => handleQuickAddNode('DECISION', 'Decision Gate')}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                padding: '7px 8px',
                backgroundColor: '#121824',
                border: '1px solid #233147',
                borderRadius: '6px',
                color: '#f8fafc',
                fontSize: '11px',
                cursor: 'pointer',
                textAlign: 'left',
              }}
              title="Añadir compuerta de decisión condicional"
            >
              <div style={{ width: '20px', height: '20px', borderRadius: '4px', backgroundColor: 'rgba(245, 158, 11, 0.2)', color: '#f59e0b', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <GitBranch size={12} />
              </div>
              <div>
                <div style={{ fontWeight: '600' }}>Decisión / IF</div>
                <div style={{ fontSize: '9px', color: '#64748b' }}>Bifurcación</div>
              </div>
            </button>

            <button
              onClick={() => handleQuickAddNode('WHILE', 'Control Bucle')}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                padding: '7px 8px',
                backgroundColor: '#121824',
                border: '1px solid #233147',
                borderRadius: '6px',
                color: '#f8fafc',
                fontSize: '11px',
                cursor: 'pointer',
                textAlign: 'left',
              }}
              title="Añadir bucle WHILE estructurado con límite de iteraciones"
            >
              <div style={{ width: '20px', height: '20px', borderRadius: '4px', backgroundColor: 'rgba(6, 182, 212, 0.2)', color: '#06b6d4', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <RotateCcw size={12} />
              </div>
              <div>
                <div style={{ fontWeight: '600' }}>Bucle WHILE</div>
                <div style={{ fontSize: '9px', color: '#64748b' }}>Iteración acotada</div>
              </div>
            </button>

            <button
              onClick={() => handleQuickAddNode('DELEGATE', 'Router de Agentes')}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                padding: '7px 8px',
                backgroundColor: '#121824',
                border: '1px solid #233147',
                borderRadius: '6px',
                color: '#f8fafc',
                fontSize: '11px',
                cursor: 'pointer',
                textAlign: 'left',
              }}
              title="Añadir router de delegación dinámica a agentes"
            >
              <div style={{ width: '20px', height: '20px', borderRadius: '4px', backgroundColor: 'rgba(168, 85, 247, 0.2)', color: '#a855f7', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Bot size={12} />
              </div>
              <div>
                <div style={{ fontWeight: '600' }}>Router</div>
                <div style={{ fontSize: '9px', color: '#64748b' }}>Delegación</div>
              </div>
            </button>

            <button
              onClick={() => handleQuickAddNode('HUMAN_APPROVAL', 'Aprobación Humana')}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                padding: '7px 8px',
                backgroundColor: '#121824',
                border: '1px solid #233147',
                borderRadius: '6px',
                color: '#f8fafc',
                fontSize: '11px',
                cursor: 'pointer',
                textAlign: 'left',
              }}
              title="Añadir pausa supervisada esperando autorización humana"
            >
              <div style={{ width: '20px', height: '20px', borderRadius: '4px', backgroundColor: 'rgba(236, 72, 153, 0.2)', color: '#ec4899', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <ShieldCheck size={12} />
              </div>
              <div>
                <div style={{ fontWeight: '600' }}>Aprobación</div>
                <div style={{ fontSize: '9px', color: '#64748b' }}>Supervisión</div>
              </div>
            </button>


            <button
              onClick={() => handleQuickAddNode('PARALLEL_FORK', 'Parallel Fork')}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                padding: '7px 8px',
                backgroundColor: '#121824',
                border: '1px solid #233147',
                borderRadius: '6px',
                color: '#f8fafc',
                fontSize: '11px',
                cursor: 'pointer',
                textAlign: 'left',
              }}
              title="Añadir bifurcación concurrente en paralelo"
            >
              <div style={{ width: '20px', height: '20px', borderRadius: '4px', backgroundColor: 'rgba(192, 132, 252, 0.2)', color: '#c084fc', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <GitFork size={12} />
              </div>
              <div>
                <div style={{ fontWeight: '600' }}>Fork</div>
                <div style={{ fontSize: '9px', color: '#64748b' }}>Paralelo</div>
              </div>
            </button>

            <button
              onClick={() => handleQuickAddNode('PARALLEL_JOIN', 'Parallel Join')}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                padding: '7px 8px',
                backgroundColor: '#121824',
                border: '1px solid #233147',
                borderRadius: '6px',
                color: '#f8fafc',
                fontSize: '11px',
                cursor: 'pointer',
                textAlign: 'left',
              }}
              title="Añadir sincronización y unión de ramas"
            >
              <div style={{ width: '20px', height: '20px', borderRadius: '4px', backgroundColor: 'rgba(129, 140, 248, 0.2)', color: '#818cf8', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <GitMerge size={12} />
              </div>
              <div>
                <div style={{ fontWeight: '600' }}>Join</div>
                <div style={{ fontSize: '9px', color: '#64748b' }}>Sincronización</div>
              </div>
            </button>

            <button
              onClick={() => handleQuickAddNode('END', 'End State')}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                padding: '7px 8px',
                backgroundColor: '#121824',
                border: '1px solid #233147',
                borderRadius: '6px',
                color: '#f8fafc',
                fontSize: '11px',
                cursor: 'pointer',
                textAlign: 'left',
              }}
              title="Añadir nodo terminal de finalización"
            >
              <div style={{ width: '20px', height: '20px', borderRadius: '4px', backgroundColor: 'rgba(244, 63, 94, 0.2)', color: '#f43f5e', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Flag size={12} />
              </div>
              <div>
                <div style={{ fontWeight: '600' }}>Fin</div>
                <div style={{ fontSize: '9px', color: '#64748b' }}>Terminal</div>
              </div>
            </button>

            <div style={{ marginTop: 'auto', borderTop: '1px solid #1e293b', paddingTop: '10px' }}>
              <button
                onClick={() => setShowConnectModal(true)}
                style={{
                  width: '100%',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: '6px',
                  padding: '6px',
                  backgroundColor: '#162234',
                  border: '1px solid #2a3c5a',
                  borderRadius: '5px',
                  color: '#38bdf8',
                  fontSize: '10.5px',
                  fontWeight: '600',
                  cursor: 'pointer',
                }}
              >
                <ArrowRight size={12} />
                <span>Unir Nodos</span>
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
              backgroundColor: '#080c14',
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
                backgroundImage: 'radial-gradient(circle, rgba(255, 255, 255, 0.04) 1px, transparent 1px)',
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
                  <path d="M 0 1 L 8 5 L 0 9 z" fill="#38bdf8" />
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
                  <path d="M 0 1 L 8 5 L 0 9 z" fill="#f59e0b" />
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
                const strokeColor = isSelected ? '#f59e0b' : (edge.condition ? '#a855f7' : '#38bdf8');

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
                        rx="4"
                        fill={isSelected ? '#1f2937' : '#0f172a'}
                        stroke={strokeColor}
                        strokeWidth="1"
                        opacity="0.9"
                      />
                      <text
                        x="0"
                        y="2"
                        fill={isSelected ? '#f59e0b' : '#cbd5e1'}
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
                    backgroundColor: '#111827',
                    border: isWiringSource
                      ? '2px solid #38bdf8'
                      : (isSelected ? '2px solid #f59e0b' : `1.5px solid ${meta.border}`),
                    borderRadius: '10px',
                    padding: '10px 12px',
                    cursor: isDragging ? 'grabbing' : 'grab',
                    boxShadow: isSelected
                      ? '0 0 16px rgba(245, 158, 11, 0.4)'
                      : (isWiringSource ? '0 0 16px rgba(56, 189, 248, 0.4)' : '0 4px 12px rgba(0, 0, 0, 0.5)'),
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
                      backgroundColor: wiringSourceId ? '#38bdf8' : '#1e293b',
                      border: '2px solid #080c14',
                      cursor: 'pointer',
                      zIndex: 35,
                      boxShadow: wiringSourceId ? '0 0 8px #38bdf8' : 'none',
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
                      backgroundColor: isWiringSource ? '#f59e0b' : '#38bdf8',
                      border: '2px solid #080c14',
                      cursor: 'pointer',
                      zIndex: 35,
                      boxShadow: '0 0 8px rgba(56, 189, 248, 0.5)',
                    }}
                  />

                  {/* Encabezado del Nodo */}
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '6px' }}>
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '4px',
                        fontSize: '9.5px',
                        fontWeight: '700',
                        padding: '2px 6px',
                        borderRadius: '4px',
                        backgroundColor: meta.bg,
                        color: meta.color,
                        border: `1px solid ${meta.border}40`,
                      }}
                    >
                      {meta.icon}
                      <span>{meta.label}</span>
                    </div>

                    <span
                      style={{
                        fontSize: '9px',
                        fontWeight: '700',
                        padding: '1px 5px',
                        borderRadius: '4px',
                        backgroundColor: statusBadge.bg,
                        color: statusBadge.color,
                      }}
                    >
                      {node.status || 'PENDING'}
                    </span>
                  </div>

                  {/* Título del Nodo */}
                  <div style={{ fontSize: '12.5px', fontWeight: '700', color: '#f8fafc', marginBottom: '4px', wordBreak: 'break-word' }}>
                    {node.name}
                  </div>

                  {/* Metadatos (Agente asignado o Herramienta) */}
                  {node.node_type === 'AGENT' && (
                    <div style={{ fontSize: '10.5px', color: '#10b981', display: 'flex', alignItems: 'center', gap: '4px', marginTop: '2px' }}>
                      <Bot size={11} />
                      <span style={{ fontFamily: 'var(--font-mono)' }}>{node.agent_id || 'Sin asignar'}</span>
                    </div>
                  )}

                  {node.node_type === 'TASK' && (
                    <div style={{ fontSize: '10.5px', color: '#38bdf8', display: 'flex', alignItems: 'center', gap: '4px', marginTop: '2px' }}>
                      <Terminal size={11} />
                      <span style={{ fontFamily: 'var(--font-mono)' }}>{node.tool_name || 'task'}</span>
                    </div>
                  )}

                  {node.node_type === 'WHILE' && (
                    <div style={{ fontSize: '10px', color: '#06b6d4', display: 'flex', alignItems: 'center', gap: '4px', marginTop: '3px' }}>
                      <RotateCcw size={11} />
                      <span>Iteración máx: {node.control_config?.max_iterations ?? 3} ({node.control_config?.on_limit || 'ABORT'})</span>
                    </div>
                  )}

                  {(node.node_type === 'IF' || node.node_type === 'DECISION') && node.control_config?.condition && (
                    <div style={{ fontSize: '9.5px', color: '#f59e0b', fontFamily: 'var(--font-mono)', marginTop: '3px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {typeof node.control_config.condition === 'string'
                        ? node.control_config.condition
                        : `${node.control_config.condition.field || ''} ${node.control_config.condition.operator || '=='} ${node.control_config.condition.expected_value ?? ''}`}
                    </div>
                  )}

                  {node.node_type === 'DELEGATE' && (
                    <div style={{ fontSize: '10px', color: '#a855f7', display: 'flex', alignItems: 'center', gap: '4px', marginTop: '3px' }}>
                      <Bot size={11} />
                      <span>Estrategia: {node.control_config?.strategy || 'MANUAL'}</span>
                    </div>
                  )}

                  {node.node_type === 'HUMAN_APPROVAL' && node.status !== 'WAITING_APPROVAL' && (
                    <div style={{ fontSize: '9.5px', color: '#ec4899', marginTop: '3px', fontStyle: 'italic', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
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
                        backgroundColor: 'rgba(236, 72, 153, 0.15)',
                        border: '1px solid rgba(236, 72, 153, 0.4)',
                        borderRadius: '6px',
                      }}
                      onClick={(e) => e.stopPropagation()}
                    >
                      <div style={{ fontSize: '9.5px', color: '#f472b6', fontWeight: '600', marginBottom: '4px' }}>
                        {node.control_config?.prompt || 'Requiere Aprobación'}
                      </div>
                      <div style={{ display: 'flex', gap: '4px' }}>
                        <button
                          onClick={() => handleApproveNode(nid, true)}
                          style={{
                            flex: 1,
                            padding: '3px 4px',
                            fontSize: '9.5px',
                            fontWeight: '700',
                            backgroundColor: '#10b981',
                            color: '#ffffff',
                            border: 'none',
                            borderRadius: '4px',
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
                            fontSize: '9.5px',
                            fontWeight: '700',
                            backgroundColor: '#ef4444',
                            color: '#ffffff',
                            border: 'none',
                            borderRadius: '4px',
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
                      borderTop: '1px solid rgba(255, 255, 255, 0.07)',
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
                        color: '#d29922',
                        fontSize: '10px',
                        fontWeight: '600',
                        cursor: 'pointer',
                        padding: '1px 3px',
                      }}
                      title="Rebobinar estado a este punto de restauración"
                    >
                      <RotateCcw size={10} />
                      <span>Backtrack</span>
                    </button>

                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDeleteNode(nid);
                      }}
                      style={{
                        background: 'none',
                        border: 'none',
                        color: '#f85149',
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
                  color: '#73849c',
                }}
              >
                <Workflow size={36} style={{ margin: '0 auto 10px', opacity: 0.4 }} />
                <div style={{ fontSize: '14px', fontWeight: '600', color: '#94a3b8' }}>
                  Lienzo de Workflow Vacío
                </div>
                <p style={{ fontSize: '12px', marginTop: '4px' }}>
                  Selecciona un nodo de la paleta izquierda para comenzar a componer el flujo de agentes.
                </p>
              </div>
            )}
            </div>
          </div>

          {/* Inspector Lateral Derecho (Propiedades de Nodo o Arista) */}
          <div
            style={{
              width: '310px',
              backgroundColor: '#0c1017',
              borderLeft: '1px solid #1e293b',
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
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid #1e293b', paddingBottom: '8px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <Settings2 size={15} style={{ color: '#38bdf8' }} />
                    <h3 style={{ fontSize: '13px', fontWeight: '700', color: '#f8fafc', margin: 0 }}>
                      Propiedades del Nodo
                    </h3>
                  </div>
                  <button
                    onClick={() => setSelectedNodeId(null)}
                    style={{ background: 'none', border: 'none', color: '#64748b', cursor: 'pointer' }}
                  >
                    <X size={14} />
                  </button>
                </div>

                <form onSubmit={handleSaveNodeProperties} style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                  <div>
                    <label style={{ fontSize: '10.5px', color: '#94a3b8', display: 'block', marginBottom: '3px' }}>ID del Nodo</label>
                    <input
                      type="text"
                      readOnly
                      value={nodeEditForm.node_id}
                      style={{ width: '100%', backgroundColor: '#111827', border: '1px solid #1e293b', borderRadius: '5px', padding: '5px 8px', fontSize: '11px', color: '#94a3b8', fontFamily: 'var(--font-mono)' }}
                    />
                  </div>

                  <div>
                    <label style={{ fontSize: '10.5px', color: '#94a3b8', display: 'block', marginBottom: '3px' }}>Nombre</label>
                    <input
                      type="text"
                      required
                      value={nodeEditForm.name}
                      onChange={(e) => setNodeEditForm({ ...nodeEditForm, name: e.target.value })}
                      style={{ width: '100%', backgroundColor: '#161c28', border: '1px solid #243044', borderRadius: '5px', padding: '5px 8px', fontSize: '11.5px', color: '#f8fafc' }}
                    />
                  </div>

                  <div>
                    <label style={{ fontSize: '10.5px', color: '#94a3b8', display: 'block', marginBottom: '3px' }}>Tipo de Nodo</label>
                    <select
                      value={nodeEditForm.node_type}
                      onChange={(e) => setNodeEditForm({ ...nodeEditForm, node_type: e.target.value })}
                      style={{ width: '100%', backgroundColor: '#161c28', border: '1px solid #243044', borderRadius: '5px', padding: '5px 8px', fontSize: '11.5px', color: '#f8fafc' }}
                    >
                      <option value="AGENT">AGENT (Agente Autónomo)</option>
                      <option value="TASK">TASK (Tarea / Herramienta)</option>
                      <option value="IF">IF (Bifurcación Condicional)</option>
                      <option value="DECISION">DECISION (Compuerta Condicional)</option>
                      <option value="WHILE">WHILE (Bucle Acotado)</option>
                      <option value="DELEGATE">DELEGATE (Router / Delegación)</option>
                      <option value="HUMAN_APPROVAL">HUMAN_APPROVAL (Aprobación Humana)</option>
                      <option value="PARALLEL_FORK">PARALLEL_FORK (Bifurcación Concurrente)</option>
                      <option value="PARALLEL_JOIN">PARALLEL_JOIN (Sincronización / Join)</option>
                      <option value="START">START (Inicio)</option>
                      <option value="END">END (Fin)</option>
                    </select>
                  </div>

                  {nodeEditForm.node_type === 'AGENT' && (
                    <div>
                      <label style={{ fontSize: '10.5px', color: '#94a3b8', display: 'block', marginBottom: '3px' }}>Agente Asignado</label>
                      <select
                        value={nodeEditForm.agent_id}
                        onChange={(e) => setNodeEditForm({ ...nodeEditForm, agent_id: e.target.value })}
                        style={{ width: '100%', backgroundColor: '#161c28', border: '1px solid #243044', borderRadius: '5px', padding: '5px 8px', fontSize: '11.5px', color: '#10b981', marginBottom: '4px' }}
                      >
                        <option value="">-- Personalizado / Escribir ID --</option>
                        {registeredAgents.map((ag) => (
                          <option key={ag.id} value={ag.id}>
                            {ag.name || ag.id} ({ag.role || 'agent'})
                          </option>
                        ))}
                      </select>
                      <input
                        type="text"
                        placeholder="O escribe agent_id personalizado..."
                        value={nodeEditForm.agent_id}
                        onChange={(e) => setNodeEditForm({ ...nodeEditForm, agent_id: e.target.value })}
                        style={{ width: '100%', backgroundColor: '#161c28', border: '1px solid #243044', borderRadius: '5px', padding: '5px 8px', fontSize: '11px', color: '#f8fafc', fontFamily: 'var(--font-mono)' }}
                      />
                    </div>
                  )}

                  {nodeEditForm.node_type === 'TASK' && (
                    <div>
                      <label style={{ fontSize: '10.5px', color: '#94a3b8', display: 'block', marginBottom: '3px' }}>Herramienta / Tool Name</label>
                      <input
                        type="text"
                        placeholder="ej. deploy_service, run_audit"
                        value={nodeEditForm.tool_name}
                        onChange={(e) => setNodeEditForm({ ...nodeEditForm, tool_name: e.target.value })}
                        style={{ width: '100%', backgroundColor: '#161c28', border: '1px solid #243044', borderRadius: '5px', padding: '5px 8px', fontSize: '11.5px', color: '#38bdf8' }}
                      />
                    </div>
                  )}

                  {/* Panel Especial: Bucle WHILE */}
                  {nodeEditForm.node_type === 'WHILE' && (
                    <div style={{ backgroundColor: '#0d1926', border: '1px solid rgba(6, 182, 212, 0.3)', borderRadius: '6px', padding: '10px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                      <div style={{ fontSize: '11px', fontWeight: '700', color: '#06b6d4', display: 'flex', alignItems: 'center', gap: '5px' }}>
                        <RotateCcw size={12} />
                        <span>Configuración de Bucle (WHILE)</span>
                      </div>
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px' }}>
                        <div>
                          <label style={{ fontSize: '10px', color: '#94a3b8', display: 'block', marginBottom: '2px' }}>Máx. Iteraciones:</label>
                          <input
                            type="number"
                            min="1"
                            max="100"
                            value={nodeEditForm.max_iterations}
                            onChange={(e) => setNodeEditForm({ ...nodeEditForm, max_iterations: e.target.value })}
                            style={{ width: '100%', backgroundColor: '#161c28', border: '1px solid #243044', borderRadius: '4px', padding: '4px 6px', fontSize: '11px', color: '#06b6d4' }}
                          />
                        </div>
                        <div>
                          <label style={{ fontSize: '10px', color: '#94a3b8', display: 'block', marginBottom: '2px' }}>Al superar límite:</label>
                          <select
                            value={nodeEditForm.on_limit}
                            onChange={(e) => setNodeEditForm({ ...nodeEditForm, on_limit: e.target.value })}
                            style={{ width: '100%', backgroundColor: '#161c28', border: '1px solid #243044', borderRadius: '4px', padding: '4px 6px', fontSize: '11px', color: '#f8fafc' }}
                          >
                            <option value="ABORT">ABORT (Detener)</option>
                            <option value="ESCALATE">ESCALATE (Escalar)</option>
                          </select>
                        </div>
                      </div>
                      <div>
                        <label style={{ fontSize: '10px', color: '#94a3b8', display: 'block', marginBottom: '2px' }}>Campo condición retorno (opcional):</label>
                        <input
                          type="text"
                          placeholder="ej. outputs.reviewer.needs_revision"
                          value={nodeEditForm.condition_field}
                          onChange={(e) => setNodeEditForm({ ...nodeEditForm, condition_field: e.target.value })}
                          style={{ width: '100%', backgroundColor: '#161c28', border: '1px solid #243044', borderRadius: '4px', padding: '4px 6px', fontSize: '11px', color: '#f8fafc', fontFamily: 'var(--font-mono)' }}
                        />
                      </div>
                    </div>
                  )}

                  {/* Panel Especial: Bifurcación Condicional IF / DECISION */}
                  {(nodeEditForm.node_type === 'IF' || nodeEditForm.node_type === 'DECISION') && (
                    <div style={{ backgroundColor: '#1c1917', border: '1px solid rgba(245, 158, 11, 0.3)', borderRadius: '6px', padding: '10px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                      <div style={{ fontSize: '11px', fontWeight: '700', color: '#f59e0b', display: 'flex', alignItems: 'center', gap: '5px' }}>
                        <GitBranch size={12} />
                        <span>Condición de Decisión (IF)</span>
                      </div>
                      <div>
                        <label style={{ fontSize: '10px', color: '#94a3b8', display: 'block', marginBottom: '2px' }}>Campo a evaluar:</label>
                        <input
                          type="text"
                          placeholder="ej. outputs.triage.category"
                          value={nodeEditForm.condition_field}
                          onChange={(e) => setNodeEditForm({ ...nodeEditForm, condition_field: e.target.value })}
                          style={{ width: '100%', backgroundColor: '#161c28', border: '1px solid #243044', borderRadius: '4px', padding: '4px 6px', fontSize: '11px', color: '#f8fafc', fontFamily: 'var(--font-mono)' }}
                        />
                      </div>
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px' }}>
                        <div>
                          <label style={{ fontSize: '10px', color: '#94a3b8', display: 'block', marginBottom: '2px' }}>Operador:</label>
                          <select
                            value={nodeEditForm.condition_operator}
                            onChange={(e) => setNodeEditForm({ ...nodeEditForm, condition_operator: e.target.value })}
                            style={{ width: '100%', backgroundColor: '#161c28', border: '1px solid #243044', borderRadius: '4px', padding: '4px 6px', fontSize: '11px', color: '#f8fafc' }}
                          >
                            <option value="==">==</option>
                            <option value="!=">!=</option>
                            <option value=">">&gt;</option>
                            <option value="<">&lt;</option>
                            <option value="contains">contains</option>
                            <option value="is_true">is_true</option>
                            <option value="is_false">is_false</option>
                          </select>
                        </div>
                        <div>
                          <label style={{ fontSize: '10px', color: '#94a3b8', display: 'block', marginBottom: '2px' }}>Valor esperado:</label>
                          <input
                            type="text"
                            placeholder="deep"
                            value={nodeEditForm.condition_expected_value}
                            onChange={(e) => setNodeEditForm({ ...nodeEditForm, condition_expected_value: e.target.value })}
                            style={{ width: '100%', backgroundColor: '#161c28', border: '1px solid #243044', borderRadius: '4px', padding: '4px 6px', fontSize: '11px', color: '#f8fafc' }}
                          />
                        </div>
                      </div>
                    </div>
                  )}

                  {/* Panel Especial: Aprobación Humana */}
                  {nodeEditForm.node_type === 'HUMAN_APPROVAL' && (
                    <div style={{ backgroundColor: '#21101d', border: '1px solid rgba(236, 72, 153, 0.3)', borderRadius: '6px', padding: '10px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                      <div style={{ fontSize: '11px', fontWeight: '700', color: '#ec4899', display: 'flex', alignItems: 'center', gap: '5px' }}>
                        <ShieldCheck size={12} />
                        <span>Compuerta de Aprobación Humana</span>
                      </div>
                      <div>
                        <label style={{ fontSize: '10px', color: '#94a3b8', display: 'block', marginBottom: '2px' }}>Prompt / Pregunta de Aprobación:</label>
                        <textarea
                          rows={2}
                          value={nodeEditForm.prompt}
                          onChange={(e) => setNodeEditForm({ ...nodeEditForm, prompt: e.target.value })}
                          placeholder="¿Autorizar la publicación del informe?"
                          style={{ width: '100%', backgroundColor: '#161c28', border: '1px solid #243044', borderRadius: '4px', padding: '4px 6px', fontSize: '11px', color: '#f8fafc', resize: 'vertical' }}
                        />
                      </div>
                      <div>
                        <label style={{ fontSize: '10px', color: '#94a3b8', display: 'block', marginBottom: '2px' }}>Timeout (segundos):</label>
                        <input
                          type="number"
                          value={nodeEditForm.timeout_seconds}
                          onChange={(e) => setNodeEditForm({ ...nodeEditForm, timeout_seconds: e.target.value })}
                          style={{ width: '100%', backgroundColor: '#161c28', border: '1px solid #243044', borderRadius: '4px', padding: '4px 6px', fontSize: '11px', color: '#ec4899' }}
                        />
                      </div>
                    </div>
                  )}

                  {/* Panel Especial: DELEGATE / Router */}
                  {nodeEditForm.node_type === 'DELEGATE' && (
                    <div style={{ backgroundColor: '#1a102b', border: '1px solid rgba(168, 85, 247, 0.3)', borderRadius: '6px', padding: '10px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                      <div style={{ fontSize: '11px', fontWeight: '700', color: '#a855f7', display: 'flex', alignItems: 'center', gap: '5px' }}>
                        <Bot size={12} />
                        <span>Router de Delegación</span>
                      </div>
                      <div>
                        <label style={{ fontSize: '10px', color: '#94a3b8', display: 'block', marginBottom: '2px' }}>Estrategia:</label>
                        <select
                          value={nodeEditForm.strategy}
                          onChange={(e) => setNodeEditForm({ ...nodeEditForm, strategy: e.target.value })}
                          style={{ width: '100%', backgroundColor: '#161c28', border: '1px solid #243044', borderRadius: '4px', padding: '4px 6px', fontSize: '11px', color: '#f8fafc' }}
                        >
                          <option value="MANUAL">MANUAL</option>
                          <option value="AUTOMATIC">AUTOMATIC</option>
                        </select>
                      </div>
                      <div>
                        <label style={{ fontSize: '10px', color: '#94a3b8', display: 'block', marginBottom: '2px' }}>Agentes Candidatos (separados por coma):</label>
                        <input
                          type="text"
                          placeholder="agent_alpha, agent_beta"
                          value={nodeEditForm.candidate_agents}
                          onChange={(e) => setNodeEditForm({ ...nodeEditForm, candidate_agents: e.target.value })}
                          style={{ width: '100%', backgroundColor: '#161c28', border: '1px solid #243044', borderRadius: '4px', padding: '4px 6px', fontSize: '11px', color: '#f8fafc' }}
                        />
                      </div>
                    </div>
                  )}

                  {/* Panel Especial: PARALLEL_JOIN */}
                  {nodeEditForm.node_type === 'PARALLEL_JOIN' && (
                    <div style={{ backgroundColor: '#13182e', border: '1px solid rgba(129, 140, 248, 0.3)', borderRadius: '6px', padding: '10px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                      <div style={{ fontSize: '11px', fontWeight: '700', color: '#818cf8', display: 'flex', alignItems: 'center', gap: '5px' }}>
                        <GitMerge size={12} />
                        <span>Sincronización Parallel Join</span>
                      </div>
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px' }}>
                        <div>
                          <label style={{ fontSize: '10px', color: '#94a3b8', display: 'block', marginBottom: '2px' }}>Política de Espera:</label>
                          <select
                            value={nodeEditForm.join_policy}
                            onChange={(e) => setNodeEditForm({ ...nodeEditForm, join_policy: e.target.value })}
                            style={{ width: '100%', backgroundColor: '#161c28', border: '1px solid #243044', borderRadius: '4px', padding: '4px 6px', fontSize: '11px', color: '#f8fafc' }}
                          >
                            <option value="all">all (Espera todas)</option>
                            <option value="any">any (Primera en llegar)</option>
                            <option value="quorum">quorum (Mayoría)</option>
                          </select>
                        </div>
                        <div>
                          <label style={{ fontSize: '10px', color: '#94a3b8', display: 'block', marginBottom: '2px' }}>Mezcla de Salidas:</label>
                          <select
                            value={nodeEditForm.merge_policy}
                            onChange={(e) => setNodeEditForm({ ...nodeEditForm, merge_policy: e.target.value })}
                            style={{ width: '100%', backgroundColor: '#161c28', border: '1px solid #243044', borderRadius: '4px', padding: '4px 6px', fontSize: '11px', color: '#f8fafc' }}
                          >
                            <option value="namespace">namespace (Aislado)</option>
                            <option value="shallow">shallow (Directo)</option>
                          </select>
                        </div>
                      </div>
                    </div>
                  )}

                  <div>
                    <label style={{ fontSize: '10.5px', color: '#94a3b8', display: 'block', marginBottom: '3px' }}>Inputs (Parámetros JSON)</label>
                    <textarea
                      rows={4}
                      value={nodeEditForm.inputs_json}
                      onChange={(e) => setNodeEditForm({ ...nodeEditForm, inputs_json: e.target.value })}
                      style={{ width: '100%', backgroundColor: '#111827', border: '1px solid #243044', borderRadius: '5px', padding: '6px', fontSize: '10.5px', color: '#f8fafc', fontFamily: 'var(--font-mono)', resize: 'vertical' }}
                    />
                  </div>

                  <div>
                    <label style={{ fontSize: '10.5px', color: '#94a3b8', display: 'block', marginBottom: '3px' }}>Resultados (Outputs)</label>
                    <textarea
                      rows={3}
                      readOnly
                      value={nodeEditForm.outputs_json}
                      style={{ width: '100%', backgroundColor: '#111827', border: '1px solid #1e293b', borderRadius: '5px', padding: '6px', fontSize: '10.5px', color: '#94a3b8', fontFamily: 'var(--font-mono)', resize: 'none' }}
                    />
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
                        backgroundColor: '#238636',
                        border: 'none',
                        borderRadius: '5px',
                        color: '#ffffff',
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
                        backgroundColor: 'rgba(244, 63, 94, 0.15)',
                        border: '1px solid rgba(244, 63, 94, 0.4)',
                        borderRadius: '5px',
                        color: '#f43f5e',
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
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid #1e293b', paddingBottom: '8px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <GitMerge size={15} style={{ color: '#c084fc' }} />
                    <h3 style={{ fontSize: '13px', fontWeight: '700', color: '#f8fafc', margin: 0 }}>
                      Función de Unión / Arista
                    </h3>
                  </div>
                  <button
                    onClick={() => setSelectedEdgeId(null)}
                    style={{ background: 'none', border: 'none', color: '#64748b', cursor: 'pointer' }}
                  >
                    <X size={14} />
                  </button>
                </div>

                <form onSubmit={handleSaveEdgeProperties} style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                  <div>
                    <label style={{ fontSize: '10.5px', color: '#94a3b8', display: 'block', marginBottom: '3px' }}>Conexión</label>
                    <div style={{ fontSize: '11px', fontFamily: 'var(--font-mono)', color: '#38bdf8', backgroundColor: '#111827', padding: '6px 8px', borderRadius: '5px' }}>
                      {edgeEditForm.from_node} &rarr; {edgeEditForm.to_node}
                    </div>
                  </div>

                  <div>
                    <label style={{ fontSize: '10.5px', color: '#94a3b8', display: 'block', marginBottom: '3px' }}>Etiqueta Visual</label>
                    <input
                      type="text"
                      placeholder="ej. Rama Éxito, Validación OK"
                      value={edgeEditForm.label}
                      onChange={(e) => setEdgeEditForm({ ...edgeEditForm, label: e.target.value })}
                      style={{ width: '100%', backgroundColor: '#161c28', border: '1px solid #243044', borderRadius: '5px', padding: '5px 8px', fontSize: '11.5px', color: '#f8fafc' }}
                    />
                  </div>

                  {/* Configuración de Condición Lógica */}
                  <div style={{ backgroundColor: '#111827', border: '1px solid #1e293b', borderRadius: '6px', padding: '10px' }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', fontWeight: '600', color: '#c084fc', cursor: 'pointer', marginBottom: '8px' }}>
                      <input
                        type="checkbox"
                        checked={edgeEditForm.has_condition}
                        onChange={(e) => setEdgeEditForm({ ...edgeEditForm, has_condition: e.target.checked })}
                      />
                      <span>Habilitar Condición Lógica (EdgeCondition)</span>
                    </label>

                    {edgeEditForm.has_condition && (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                        <div>
                          <label style={{ fontSize: '10px', color: '#94a3b8', display: 'block', marginBottom: '2px' }}>
                            Campo a evaluar (ej. output.status, score):
                          </label>
                          <input
                            type="text"
                            value={edgeEditForm.field}
                            onChange={(e) => setEdgeEditForm({ ...edgeEditForm, field: e.target.value })}
                            style={{ width: '100%', backgroundColor: '#161c28', border: '1px solid #243044', borderRadius: '4px', padding: '4px 6px', fontSize: '11px', color: '#f8fafc', fontFamily: 'var(--font-mono)' }}
                          />
                        </div>

                        <div>
                          <label style={{ fontSize: '10px', color: '#94a3b8', display: 'block', marginBottom: '2px' }}>
                            Operador:
                          </label>
                          <select
                            value={edgeEditForm.operator}
                            onChange={(e) => setEdgeEditForm({ ...edgeEditForm, operator: e.target.value })}
                            style={{ width: '100%', backgroundColor: '#161c28', border: '1px solid #243044', borderRadius: '4px', padding: '4px 6px', fontSize: '11px', color: '#f8fafc' }}
                          >
                            <option value="==">== (Igual a)</option>
                            <option value="!=">!= (Distinto de)</option>
                            <option value=">">&gt; (Mayor que)</option>
                            <option value=">=">&gt;= (Mayor o igual)</option>
                            <option value="<">&lt; (Menor que)</option>
                            <option value="<=">&lt;= (Menor o igual)</option>
                            <option value="in">in (Contenido en lista)</option>
                            <option value="contains">contains (Contiene substring/elemento)</option>
                            <option value="is_true">is_true (Verdadero)</option>
                            <option value="is_false">is_false (Falso)</option>
                          </select>
                        </div>

                        {edgeEditForm.operator !== 'is_true' && edgeEditForm.operator !== 'is_false' && (
                          <div>
                            <label style={{ fontSize: '10px', color: '#94a3b8', display: 'block', marginBottom: '2px' }}>
                              Valor Esperado:
                            </label>
                            <input
                              type="text"
                              value={edgeEditForm.expected_value}
                              onChange={(e) => setEdgeEditForm({ ...edgeEditForm, expected_value: e.target.value })}
                              style={{ width: '100%', backgroundColor: '#161c28', border: '1px solid #243044', borderRadius: '4px', padding: '4px 6px', fontSize: '11px', color: '#f8fafc', fontFamily: 'var(--font-mono)' }}
                            />
                          </div>
                        )}

                        <div style={{ fontSize: '9.5px', color: '#64748b', fontStyle: 'italic', marginTop: '2px' }}>
                          Se transitará a {edgeEditForm.to_node} solo si context.{edgeEditForm.field} {edgeEditForm.operator} {edgeEditForm.expected_value}.
                        </div>
                      </div>
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
                        backgroundColor: '#238636',
                        border: 'none',
                        borderRadius: '5px',
                        color: '#ffffff',
                        fontSize: '11px',
                        fontWeight: '600',
                        cursor: 'pointer',
                      }}
                    >
                      <Save size={12} />
                      <span>Guardar Unión</span>
                    </button>

                    <button
                      type="button"
                      onClick={() => handleDeleteEdge(edgeEditForm.edge_id)}
                      style={{
                        padding: '6px 10px',
                        backgroundColor: 'rgba(244, 63, 94, 0.15)',
                        border: '1px solid rgba(244, 63, 94, 0.4)',
                        borderRadius: '5px',
                        color: '#f43f5e',
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
                <div style={{ borderBottom: '1px solid #1e293b', paddingBottom: '8px' }}>
                  <div style={{ fontSize: '11px', fontWeight: '700', textTransform: 'uppercase', color: '#64748b' }}>
                    Flujo Activo
                  </div>
                  <h3 style={{ fontSize: '13.5px', fontWeight: '700', color: '#f8fafc', margin: '4px 0' }}>
                    {workflow?.name || 'Selecciona un flujo'}
                  </h3>
                  <p style={{ fontSize: '11px', color: '#94a3b8', margin: 0 }}>
                    {workflow?.description || 'Sin descripción'}
                  </p>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }}>
                  <div style={{ backgroundColor: '#111827', padding: '8px', borderRadius: '5px', border: '1px solid #1e293b' }}>
                    <div style={{ fontSize: '10px', color: '#64748b' }}>Nodos</div>
                    <div style={{ fontSize: '14px', fontWeight: '700', color: '#38bdf8' }}>{nodesArray.length}</div>
                  </div>
                  <div style={{ backgroundColor: '#111827', padding: '8px', borderRadius: '5px', border: '1px solid #1e293b' }}>
                    <div style={{ fontSize: '10px', color: '#64748b' }}>Conexiones</div>
                    <div style={{ fontSize: '14px', fontWeight: '700', color: '#c084fc' }}>{edgesArray.length}</div>
                  </div>
                </div>

                <div style={{ backgroundColor: '#111827', padding: '10px', borderRadius: '6px', border: '1px solid #1e293b' }}>
                  <div style={{ fontSize: '10.5px', fontWeight: '600', color: '#38bdf8', marginBottom: '6px', display: 'flex', alignItems: 'center', gap: '4px' }}>
                    <Info size={12} />
                    <span>Instrucciones Rápidas</span>
                  </div>
                  <ul style={{ fontSize: '10px', color: '#94a3b8', paddingLeft: '14px', margin: 0, display: 'flex', flexDirection: 'column', gap: '4px' }}>
                    <li><strong>Crear nodo:</strong> Haz clic en cualquier botón de la paleta izquierda.</li>
                    <li><strong>Editar propiedades:</strong> Haz clic sobre cualquier nodo para asignarle agente, inputs y rol.</li>
                    <li><strong>Conectar nodos:</strong> Haz clic en el círculo derecho (salida) de un nodo y luego en el círculo izquierdo de otro.</li>
                    <li><strong>Configurar uniones:</strong> Haz clic en cualquier cable/arista para editar su condición lógica (EdgeCondition).</li>
                    <li><strong>Mover nodos:</strong> Arrastra y suelta libremente sobre el lienzo.</li>
                  </ul>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Barra Inferior de Telemetría y Logs */}
      <div
        style={{
          height: '65px',
          backgroundColor: '#0c1017',
          borderTop: '1px solid #1e293b',
          padding: '6px 14px',
          overflowY: 'auto',
          fontSize: '10.5px',
          fontFamily: 'var(--font-mono)',
          color: '#94a3b8',
          display: 'flex',
          flexDirection: 'column',
          gap: '2px',
          flexShrink: 0,
        }}
      >
        {logs.map((log, idx) => (
          <div key={idx} style={{ display: 'flex', gap: '8px', lineHeight: 1.3 }}>
            <span style={{ color: '#64748b' }}>[{log.time}]</span>
            <span style={{ color: log.tag === 'ERROR' ? '#f43f5e' : (log.tag === 'VALID' ? '#10b981' : '#38bdf8'), fontWeight: '600' }}>
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
            backgroundColor: 'rgba(0,0,0,0.75)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
          }}
        >
          <div
            style={{
              backgroundColor: '#121824',
              border: '1px solid #233147',
              borderRadius: '10px',
              padding: '20px',
              width: '400px',
              display: 'flex',
              flexDirection: 'column',
              gap: '12px',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <h3 style={{ fontSize: '14.5px', fontWeight: '700', color: '#f8fafc', margin: 0 }}>
                Nuevo Flujo de Trabajo
              </h3>
              <button onClick={() => setShowCreateWfModal(false)} style={{ background: 'none', border: 'none', color: '#94a3b8', cursor: 'pointer' }}>
                <X size={16} />
              </button>
            </div>
            <form onSubmit={handleCreateWorkflow} style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              <div>
                <label style={{ fontSize: '11px', color: '#94a3b8', display: 'block', marginBottom: '3px' }}>Nombre:</label>
                <input
                  type="text"
                  required
                  value={newWfName}
                  onChange={(e) => setNewWfName(e.target.value)}
                  placeholder="ej. Pipeline de Auditoría y Fix"
                  style={{ width: '100%', backgroundColor: '#0c1017', border: '1px solid #243044', borderRadius: '6px', padding: '6px 10px', fontSize: '12px', color: '#f8fafc' }}
                />
              </div>
              <div>
                <label style={{ fontSize: '11px', color: '#94a3b8', display: 'block', marginBottom: '3px' }}>Descripción:</label>
                <input
                  type="text"
                  value={newWfDesc}
                  onChange={(e) => setNewWfDesc(e.target.value)}
                  placeholder="Descripción del objetivo del flujo"
                  style={{ width: '100%', backgroundColor: '#0c1017', border: '1px solid #243044', borderRadius: '6px', padding: '6px 10px', fontSize: '12px', color: '#f8fafc' }}
                />
              </div>
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '6px' }}>
                <button type="button" onClick={() => setShowCreateWfModal(false)} style={{ padding: '6px 12px', borderRadius: '5px', backgroundColor: '#1e293b', border: 'none', color: '#f8fafc', fontSize: '11.5px', cursor: 'pointer' }}>
                  Cancelar
                </button>
                <button type="submit" style={{ padding: '6px 14px', borderRadius: '5px', backgroundColor: '#238636', border: 'none', color: '#ffffff', fontSize: '11.5px', fontWeight: '600', cursor: 'pointer' }}>
                  Crear Flujo
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
            backgroundColor: 'rgba(0,0,0,0.75)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
          }}
        >
          <div
            style={{
              backgroundColor: '#121824',
              border: '1px solid #233147',
              borderRadius: '10px',
              padding: '20px',
              width: '420px',
              display: 'flex',
              flexDirection: 'column',
              gap: '12px',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <h3 style={{ fontSize: '14.5px', fontWeight: '700', color: '#f8fafc', margin: 0 }}>
                Añadir Nodo al Flujo
              </h3>
              <button onClick={() => setShowAddNodeModal(false)} style={{ background: 'none', border: 'none', color: '#94a3b8', cursor: 'pointer' }}>
                <X size={16} />
              </button>
            </div>
            <form onSubmit={handleAddNode} style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              <div>
                <label style={{ fontSize: '11px', color: '#94a3b8', display: 'block', marginBottom: '3px' }}>Nombre del Nodo:</label>
                <input
                  type="text"
                  required
                  value={createNodeForm.name}
                  onChange={(e) => setCreateNodeForm({ ...createNodeForm, name: e.target.value })}
                  placeholder="ej. Auditoría de Seguridad"
                  style={{ width: '100%', backgroundColor: '#0c1017', border: '1px solid #243044', borderRadius: '6px', padding: '6px 10px', fontSize: '12px', color: '#f8fafc' }}
                />
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }}>
                <div>
                  <label style={{ fontSize: '11px', color: '#94a3b8', display: 'block', marginBottom: '3px' }}>Tipo de Nodo:</label>
                  <select
                    value={createNodeForm.node_type}
                    onChange={(e) => setCreateNodeForm({ ...createNodeForm, node_type: e.target.value })}
                    style={{ width: '100%', backgroundColor: '#0c1017', border: '1px solid #243044', borderRadius: '6px', padding: '6px 10px', fontSize: '12px', color: '#f8fafc' }}
                  >
                    <option value="AGENT">AGENT (Agente IA)</option>
                    <option value="TASK">TASK (Tarea / Herramienta)</option>
                    <option value="IF">IF (Bifurcación Condicional)</option>
                    <option value="DECISION">DECISION (Compuerta)</option>
                    <option value="WHILE">WHILE (Bucle Acotado)</option>
                    <option value="DELEGATE">DELEGATE (Router)</option>
                    <option value="HUMAN_APPROVAL">HUMAN_APPROVAL (Aprobación Humana)</option>
                    <option value="PARALLEL_FORK">PARALLEL_FORK (Fork)</option>
                    <option value="PARALLEL_JOIN">PARALLEL_JOIN (Join)</option>
                    <option value="END">END (Fin)</option>
                  </select>
                </div>
                <div>
                  <label style={{ fontSize: '11px', color: '#94a3b8', display: 'block', marginBottom: '3px' }}>Agente Asignado:</label>
                  {createNodeForm.node_type === 'AGENT' ? (
                    <select
                      value={createNodeForm.agent_id}
                      onChange={(e) => setCreateNodeForm({ ...createNodeForm, agent_id: e.target.value })}
                      style={{ width: '100%', backgroundColor: '#0c1017', border: '1px solid #243044', borderRadius: '6px', padding: '6px 10px', fontSize: '12px', color: '#10b981' }}
                    >
                      <option value="">(Selecciona o escribe)</option>
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
                      placeholder="N/A"
                      style={{ width: '100%', backgroundColor: '#0c1017', border: '1px solid #1e293b', borderRadius: '6px', padding: '6px 10px', fontSize: '12px', color: '#64748b' }}
                    />
                  )}
                </div>
              </div>

              {createNodeForm.node_type === 'AGENT' && (
                <div>
                  <label style={{ fontSize: '11px', color: '#94a3b8', display: 'block', marginBottom: '3px' }}>ID de Agente Personalizado (opcional):</label>
                  <input
                    type="text"
                    value={createNodeForm.agent_id}
                    onChange={(e) => setCreateNodeForm({ ...createNodeForm, agent_id: e.target.value })}
                    placeholder="ej. ag_security_auditor"
                    style={{ width: '100%', backgroundColor: '#0c1017', border: '1px solid #243044', borderRadius: '6px', padding: '6px 10px', fontSize: '12px', color: '#f8fafc', fontFamily: 'var(--font-mono)' }}
                  />
                </div>
              )}

              {createNodeForm.node_type === 'TASK' && (
                <div>
                  <label style={{ fontSize: '11px', color: '#94a3b8', display: 'block', marginBottom: '3px' }}>Herramienta / Tool Name:</label>
                  <input
                    type="text"
                    value={createNodeForm.tool_name}
                    onChange={(e) => setCreateNodeForm({ ...createNodeForm, tool_name: e.target.value })}
                    placeholder="ej. deploy_service"
                    style={{ width: '100%', backgroundColor: '#0c1017', border: '1px solid #243044', borderRadius: '6px', padding: '6px 10px', fontSize: '12px', color: '#f8fafc' }}
                  />
                </div>
              )}

              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '6px' }}>
                <button type="button" onClick={() => setShowAddNodeModal(false)} style={{ padding: '6px 12px', borderRadius: '5px', backgroundColor: '#1e293b', border: 'none', color: '#f8fafc', fontSize: '11.5px', cursor: 'pointer' }}>
                  Cancelar
                </button>
                <button type="submit" style={{ padding: '6px 14px', borderRadius: '5px', backgroundColor: '#238636', border: 'none', color: '#ffffff', fontSize: '11.5px', fontWeight: '600', cursor: 'pointer' }}>
                  Añadir al Lienzo
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
            backgroundColor: 'rgba(0,0,0,0.75)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
          }}
        >
          <div
            style={{
              backgroundColor: '#121824',
              border: '1px solid #233147',
              borderRadius: '10px',
              padding: '20px',
              width: '450px',
              display: 'flex',
              flexDirection: 'column',
              gap: '12px',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <h3 style={{ fontSize: '14.5px', fontWeight: '700', color: '#f8fafc', margin: 0 }}>
                Conectar Nodos (Crear Unión / Arista)
              </h3>
              <button onClick={() => setShowConnectModal(false)} style={{ background: 'none', border: 'none', color: '#94a3b8', cursor: 'pointer' }}>
                <X size={16} />
              </button>
            </div>
            <form onSubmit={handleConnectNodesModal} style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }}>
                <div>
                  <label style={{ fontSize: '11px', color: '#94a3b8', display: 'block', marginBottom: '3px' }}>Nodo Origen:</label>
                  <select
                    required
                    value={connectForm.from_node}
                    onChange={(e) => setConnectForm({ ...connectForm, from_node: e.target.value })}
                    style={{ width: '100%', backgroundColor: '#0c1017', border: '1px solid #243044', borderRadius: '6px', padding: '6px 10px', fontSize: '12px', color: '#f8fafc' }}
                  >
                    <option value="">Seleccionar...</option>
                    {nodesArray.map((n) => {
                      const nid = n.node_id || n.id;
                      return <option key={nid} value={nid}>{n.name} ({nid})</option>;
                    })}
                  </select>
                </div>
                <div>
                  <label style={{ fontSize: '11px', color: '#94a3b8', display: 'block', marginBottom: '3px' }}>Nodo Destino:</label>
                  <select
                    required
                    value={connectForm.to_node}
                    onChange={(e) => setConnectForm({ ...connectForm, to_node: e.target.value })}
                    style={{ width: '100%', backgroundColor: '#0c1017', border: '1px solid #243044', borderRadius: '6px', padding: '6px 10px', fontSize: '12px', color: '#f8fafc' }}
                  >
                    <option value="">Seleccionar...</option>
                    {nodesArray.map((n) => {
                      const nid = n.node_id || n.id;
                      return <option key={nid} value={nid}>{n.name} ({nid})</option>;
                    })}
                  </select>
                </div>
              </div>
              <div>
                <label style={{ fontSize: '11px', color: '#94a3b8', display: 'block', marginBottom: '3px' }}>Etiqueta (opcional):</label>
                <input
                  type="text"
                  value={connectForm.label}
                  onChange={(e) => setConnectForm({ ...connectForm, label: e.target.value })}
                  placeholder="ej. Si status == SUCCESS"
                  style={{ width: '100%', backgroundColor: '#0c1017', border: '1px solid #243044', borderRadius: '6px', padding: '6px 10px', fontSize: '12px', color: '#f8fafc' }}
                />
              </div>

              {/* Condición lógica */}
              <div style={{ backgroundColor: '#0c1017', border: '1px solid #243044', borderRadius: '6px', padding: '10px' }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', fontWeight: '600', color: '#c084fc', cursor: 'pointer' }}>
                  <input
                    type="checkbox"
                    checked={connectForm.has_condition}
                    onChange={(e) => setConnectForm({ ...connectForm, has_condition: e.target.checked })}
                  />
                  <span>Aplicar Condición Lógica de Unión (Branching)</span>
                </label>

                {connectForm.has_condition && (
                  <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 0.8fr 1fr', gap: '6px', marginTop: '8px' }}>
                    <div>
                      <label style={{ fontSize: '10px', color: '#94a3b8', display: 'block', marginBottom: '2px' }}>Campo:</label>
                      <input
                        type="text"
                        value={connectForm.field}
                        onChange={(e) => setConnectForm({ ...connectForm, field: e.target.value })}
                        placeholder="output.status"
                        style={{ width: '100%', backgroundColor: '#161c28', border: '1px solid #243044', borderRadius: '4px', padding: '4px 6px', fontSize: '11px', color: '#f8fafc' }}
                      />
                    </div>
                    <div>
                      <label style={{ fontSize: '10px', color: '#94a3b8', display: 'block', marginBottom: '2px' }}>Operador:</label>
                      <select
                        value={connectForm.operator}
                        onChange={(e) => setConnectForm({ ...connectForm, operator: e.target.value })}
                        style={{ width: '100%', backgroundColor: '#161c28', border: '1px solid #243044', borderRadius: '4px', padding: '4px 6px', fontSize: '11px', color: '#f8fafc' }}
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
                      <label style={{ fontSize: '10px', color: '#94a3b8', display: 'block', marginBottom: '2px' }}>Valor:</label>
                      <input
                        type="text"
                        value={connectForm.expected_value}
                        onChange={(e) => setConnectForm({ ...connectForm, expected_value: e.target.value })}
                        placeholder="SUCCESS"
                        style={{ width: '100%', backgroundColor: '#161c28', border: '1px solid #243044', borderRadius: '4px', padding: '4px 6px', fontSize: '11px', color: '#f8fafc' }}
                      />
                    </div>
                  </div>
                )}
              </div>

              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '6px' }}>
                <button type="button" onClick={() => setShowConnectModal(false)} style={{ padding: '6px 12px', borderRadius: '5px', backgroundColor: '#1e293b', border: 'none', color: '#f8fafc', fontSize: '11.5px', cursor: 'pointer' }}>
                  Cancelar
                </button>
                <button type="submit" style={{ padding: '6px 14px', borderRadius: '5px', backgroundColor: '#238636', border: 'none', color: '#ffffff', fontSize: '11.5px', fontWeight: '600', cursor: 'pointer' }}>
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
            backgroundColor: 'rgba(0,0,0,0.8)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
          }}
        >
          <div
            style={{
              backgroundColor: '#101522',
              border: '1px solid #28354d',
              borderRadius: '12px',
              padding: '24px',
              width: '680px',
              maxWidth: '92vw',
              maxHeight: '85vh',
              display: 'flex',
              flexDirection: 'column',
              gap: '16px',
              boxShadow: '0 20px 40px rgba(0,0,0,0.6)',
              overflowY: 'auto',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid #1e293b', paddingBottom: '12px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <div style={{ width: '28px', height: '28px', borderRadius: '6px', backgroundColor: 'rgba(165, 180, 252, 0.15)', color: '#a5b4fc', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <LayoutTemplate size={16} />
                </div>
                <div>
                  <h3 style={{ fontSize: '15px', fontWeight: '700', color: '#f8fafc', margin: 0 }}>
                    Plantillas Canónicas de Orquestación
                  </h3>
                  <p style={{ fontSize: '11px', color: '#94a3b8', margin: 0 }}>
                    Flujos de control multiagente preconfigurados según la semántica PRAXEON
                  </p>
                </div>
              </div>
              <button
                onClick={() => setShowTemplatesModal(false)}
                style={{ background: 'none', border: 'none', color: '#94a3b8', cursor: 'pointer', padding: '4px' }}
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
                      backgroundColor: '#161d2d',
                      border: '1px solid #233148',
                      borderRadius: '8px',
                      padding: '14px 16px',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '8px',
                      transition: 'border-color 0.2s ease',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span style={{ fontSize: '13.5px', fontWeight: '700', color: '#f8fafc' }}>
                          {tpl.name}
                        </span>
                        <span
                          style={{
                            fontSize: '9.5px',
                            fontWeight: '600',
                            padding: '2px 6px',
                            borderRadius: '4px',
                            backgroundColor: 'rgba(56, 189, 248, 0.15)',
                            color: '#38bdf8',
                            fontFamily: 'var(--font-mono)',
                          }}
                        >
                          {tpl.template_id || tpl.key}
                        </span>
                      </div>
                      <div style={{ fontSize: '11px', color: '#64748b' }}>
                        {tpl.node_count} nodos &bull; {tpl.edge_count} aristas
                      </div>
                    </div>

                    <p style={{ fontSize: '11.5px', color: '#94a3b8', margin: 0, lineHeight: 1.4 }}>
                      {tpl.description}
                    </p>

                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: '4px', paddingTop: '8px', borderTop: '1px solid rgba(255,255,255,0.06)' }}>
                      <div style={{ display: 'flex', gap: '6px' }}>
                        {(tpl.template_id || tpl.key) === 'code_review_loop' && (
                          <span style={{ fontSize: '10px', color: '#06b6d4', backgroundColor: 'rgba(6, 182, 212, 0.12)', padding: '2px 6px', borderRadius: '4px' }}>
                            Bucle Acotado (WHILE &le; 3)
                          </span>
                        )}
                        {(tpl.template_id || tpl.key) === 'research_writer_reviewer' && (
                          <span style={{ fontSize: '10px', color: '#ec4899', backgroundColor: 'rgba(236, 72, 153, 0.12)', padding: '2px 6px', borderRadius: '4px' }}>
                            Compuerta Humana (Approval)
                          </span>
                        )}
                        {(tpl.template_id || tpl.key) === 'triage_router' && (
                          <span style={{ fontSize: '10px', color: '#a855f7', backgroundColor: 'rgba(168, 85, 247, 0.12)', padding: '2px 6px', borderRadius: '4px' }}>
                            Router IF + Parallel Join
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
                          borderRadius: '6px',
                          backgroundColor: '#2563eb',
                          border: 'none',
                          color: '#ffffff',
                          fontSize: '11px',
                          fontWeight: '600',
                          cursor: 'pointer',
                        }}
                      >
                        <Plus size={12} />
                        <span>Usar Plantilla</span>
                      </button>
                    </div>
                  </div>
                ))
              ) : (
                <div style={{ textAlign: 'center', padding: '30px', color: '#64748b' }}>
                  <LayoutTemplate size={32} style={{ margin: '0 auto 8px', opacity: 0.5 }} />
                  <div>No se han cargado las plantillas o el backend no está disponible.</div>
                  <button
                    onClick={() => loadTemplates()}
                    style={{
                      marginTop: '10px',
                      padding: '5px 12px',
                      borderRadius: '5px',
                      backgroundColor: '#1e293b',
                      border: '1px solid #334155',
                      color: '#f8fafc',
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

