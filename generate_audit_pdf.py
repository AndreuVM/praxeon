"""
Script para generar el Informe de Auditoría Técnica Integral y Seguridad de Praxeon v1.0.0 en formato PDF.
Versión optimizada con maquetación balanceada de 5 páginas sin desbordamientos ni glifos corruptos.
"""

import sys
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, HRFlowable
)
from reportlab.pdfgen import canvas

class NumberedCanvas(canvas.Canvas):
    """
    Canvas de doble pasada para calcular el total de páginas y dibujar
    encabezados y pies de página corporativos profesionales.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_decorations(self, page_count):
        if self._pageNumber == 1:
            # Portada: diseño con franja corporativa lateral
            self.saveState()
            self.setFillColor(colors.HexColor("#1A2B4C"))
            self.rect(0, 0, 18, 842, fill=1, stroke=0)
            self.setFillColor(colors.HexColor("#C0392B"))
            self.rect(18, 0, 6, 842, fill=1, stroke=0)
            self.restoreState()
            return

        self.saveState()
        # Encabezado corporativo
        self.setFont("Helvetica-Bold", 8)
        self.setFillColor(colors.HexColor("#1A2B4C"))
        self.drawString(54, 842 - 36, "PRAXEON v1.0.0")
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#5D6D7E"))
        self.drawString(132, 842 - 36, "|   INFORME DE AUDITORÍA TÉCNICA INTEGRAL Y SEGURIDAD")
        
        self.setStrokeColor(colors.HexColor("#D5D8DC"))
        self.setLineWidth(0.6)
        self.line(54, 842 - 42, 595 - 54, 842 - 42)

        # Pie de página corporativo
        self.line(54, 45, 595 - 54, 45)
        self.setFont("Helvetica-Bold", 8)
        self.setFillColor(colors.HexColor("#C0392B"))
        self.drawString(54, 32, "CONFIDENCIAL")
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#7F8C8D"))
        self.drawString(125, 32, "— Evaluación Técnica de Arquitectura, Seguridad y Runtime")
        page_str = f"Página {self._pageNumber} de {page_count}"
        self.drawRightString(595 - 54, 32, page_str)
        self.restoreState()


def build_audit_pdf(output_filename: str):
    doc = SimpleDocTemplate(
        output_filename,
        pagesize=A4,
        leftMargin=54,
        rightMargin=54,
        topMargin=54,
        bottomMargin=54
    )

    styles = getSampleStyleSheet()

    # Paleta de colores
    PRIMARY = colors.HexColor("#1A2B4C")    # Azul Marino Profundo
    SECONDARY = colors.HexColor("#2E5B88")  # Azul Acero
    DANGER = colors.HexColor("#C0392B")     # Rojo Carmesí
    WARNING = colors.HexColor("#D4AC0D")    # Ámbar
    SUCCESS = colors.HexColor("#27AE60")    # Verde
    DARK = colors.HexColor("#2C3E50")       # Gris Oscuro Texto
    MUTED = colors.HexColor("#7F8C8D")      # Gris Claro
    BG_ALT = colors.HexColor("#F8F9FA")     # Fondo Alterno Tablas

    # Estilos tipográficos
    title_style = ParagraphStyle(
        'CoverTitle',
        fontName='Helvetica-Bold',
        fontSize=24,
        leading=28,
        textColor=PRIMARY,
        spaceAfter=8
    )
    
    subtitle_style = ParagraphStyle(
        'CoverSubtitle',
        fontName='Helvetica',
        fontSize=12,
        leading=16,
        textColor=SECONDARY,
        spaceAfter=18
    )

    meta_style = ParagraphStyle(
        'CoverMeta',
        fontName='Helvetica',
        fontSize=9,
        leading=13.5,
        textColor=DARK
    )

    h1_style = ParagraphStyle(
        'Header1',
        fontName='Helvetica-Bold',
        fontSize=14,
        leading=18,
        textColor=PRIMARY,
        spaceBefore=10,
        spaceAfter=6,
        keepWithNext=True
    )

    h2_style = ParagraphStyle(
        'Header2',
        fontName='Helvetica-Bold',
        fontSize=10.5,
        leading=14,
        textColor=SECONDARY,
        spaceBefore=8,
        spaceAfter=4,
        keepWithNext=True
    )

    body_style = ParagraphStyle(
        'BodyTextCustom',
        fontName='Helvetica',
        fontSize=8.5,
        leading=11.6,
        textColor=DARK,
        spaceAfter=5
    )

    code_style = ParagraphStyle(
        'CodeSnippet',
        fontName='Courier',
        fontSize=7.6,
        leading=9.8,
        textColor=colors.HexColor("#900C3F"),
        backColor=colors.HexColor("#F4F6F6"),
        spaceBefore=3,
        spaceAfter=5
    )

    callout_danger_style = ParagraphStyle(
        'CalloutDanger',
        fontName='Helvetica',
        fontSize=8.3,
        leading=11.5,
        textColor=colors.HexColor("#78281F")
    )

    callout_success_style = ParagraphStyle(
        'CalloutSuccess',
        fontName='Helvetica',
        fontSize=8.3,
        leading=11.5,
        textColor=colors.HexColor("#196F3D")
    )

    table_header_style = ParagraphStyle(
        'TableHeader',
        fontName='Helvetica-Bold',
        fontSize=7.8,
        leading=10,
        textColor=colors.white
    )

    table_cell_style = ParagraphStyle(
        'TableCell',
        fontName='Helvetica',
        fontSize=7.6,
        leading=9.8,
        textColor=DARK
    )

    table_cell_bold = ParagraphStyle(
        'TableCellBold',
        fontName='Helvetica-Bold',
        fontSize=7.6,
        leading=9.8,
        textColor=DARK
    )

    story = []

    # =========================================================================
    # PÁGINA 1: PORTADA Y RESUMEN EJECUTIVO
    # =========================================================================
    story.append(Spacer(1, 30))
    story.append(Paragraph("INFORME DE AUDITORÍA TÉCNICA INTEGRAL", title_style))
    story.append(Paragraph("Auditoría de Arquitectura, Código, Seguridad de Runtime y Empaquetado", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=2.5, color=DANGER, spaceBefore=0, spaceAfter=16))

    cover_meta_text = """
    <b>Sistema Auditado:</b> Praxeon (JEV Reasoning Navigator / TypeSafe AI)<br/>
    <b>Versión del Software:</b> 1.0.0 (Release Candidate)<br/>
    <b>Directorio Base:</b> <code>scratch/jev-llm/jev-reasoning-navigator</code><br/>
    <b>Fecha de Emisión:</b> 28 de Septiembre de 2026<br/>
    <b>Clasificación:</b> <b>CONFIDENCIAL / DISTRIBUCIÓN RESTRINGIDA</b><br/>
    <b>Entorno de Ejecución:</b> Windows 11 / Python 3.11.15 / Node.js 22 / Vite 8.3.1<br/>
    <b>Suite de Pruebas:</b> 284 Passed, 1 Skipped, 1 Warning (61.72s)
    """
    story.append(Paragraph(cover_meta_text, meta_style))
    story.append(Spacer(1, 16))

    verdict_html = """
    <b>DICTAMEN DE PRODUCCIÓN: NO APTO PARA DESPLIEGUE EN PRODUCCIÓN (RECHAZADO)</b><br/><br/>
    El software implementa conceptos de vanguardia en gobernanza de agentes autónomos (capabilities criptográficos HMAC, 
    máquinas de decisión finitas y registro de no-repetición). Sin embargo, la auditoría ha descubierto <b>bloqueos de ejecución 
    sistemáticos en tiempo real (BUG-01)</b>, una <b>vulnerabilidad crítica de bypass de autenticación 'Fail-Open' (VULN-01)</b> 
    y la <b>distribución de paquetes wheel rotos que devuelven HTTP 404 (BUG-03)</b>. El sistema requiere la aplicación 
    estricta del plan de remediación antes de cualquier puesta en servicio comercial o industrial.
    """
    verdict_table = Table(
        [[Paragraph(verdict_html, callout_danger_style)]],
        colWidths=[485]
    )
    verdict_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#FDEDEC")),
        ('BOX', (0,0), (-1,-1), 1.2, DANGER),
        ('PADDING', (0,0), (-1,-1), 9),
    ]))
    story.append(verdict_table)
    story.append(Spacer(1, 16))

    summary_data = [
        [Paragraph("Categoría Auditada", table_header_style), Paragraph("Total", table_header_style), Paragraph("Críticos (P0)", table_header_style), Paragraph("Altos (P1)", table_header_style), Paragraph("Medios (P2)", table_header_style)],
        [Paragraph("Vulnerabilidades de Seguridad", table_cell_bold), Paragraph("4", table_cell_style), Paragraph("1 (VULN-01)", table_cell_style), Paragraph("2 (VULN-02, VULN-03)", table_cell_style), Paragraph("1 (VULN-04)", table_cell_style)],
        [Paragraph("Bugs Funcionales y Runtime", table_cell_bold), Paragraph("5", table_cell_style), Paragraph("2 (BUG-01, BUG-03)", table_cell_style), Paragraph("2 (BUG-02, BUG-04)", table_cell_style), Paragraph("1 (BUG-05)", table_cell_style)],
        [Paragraph("Deuda Técnica y Arquitectura", table_cell_bold), Paragraph("4", table_cell_style), Paragraph("1 (Dual Bus)", table_cell_style), Paragraph("2 (Bypass Proxy, Concurrencia)", table_cell_style), Paragraph("1 (Modelos)", table_cell_style)],
        [Paragraph("Empaquetado y Dependencias", table_cell_bold), Paragraph("3", table_cell_style), Paragraph("1 (Assets SPA)", table_cell_style), Paragraph("1 (Deps CLI)", table_cell_style), Paragraph("1 (utcnow)", table_cell_style)],
    ]
    t_sum = Table(summary_data, colWidths=[150, 55, 95, 95, 90])
    t_sum.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), PRIMARY),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#D5D8DC")),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, BG_ALT]),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(t_sum)

    # SALTO A PÁGINA 2
    story.append(PageBreak())

    # =========================================================================
    # PÁGINA 2: CONTRASTE CRÍTICO Y ANÁLISIS ARQUITECTÓNICO
    # =========================================================================
    story.append(Paragraph("1. Validación y Contraste Crítico de Hallazgos", h1_style))
    story.append(HRFlowable(width="100%", thickness=1, color=SECONDARY, spaceBefore=1, spaceAfter=6))
    story.append(Paragraph(
        "A continuación se documenta el contraste riguroso entre la investigación preliminar y la inspección empírica "
        "directa del código fuente, demostrando dónde se pasaron por alto fallos estructurales de distribución y concurrencia.",
        body_style
    ))

    disc_data = [
        [Paragraph("Componente", table_header_style), Paragraph("Diagnóstico Previo", table_header_style), Paragraph("Realidad en Código", table_header_style), Paragraph("Impacto Corregido", table_header_style)],
        
        [Paragraph("<b>Empaquetado Wheel</b>", table_cell_style),
         Paragraph("Señaló fallo de ruta en tests hacia <code>../dist/*.whl</code>.", table_cell_style),
         Paragraph("<code>pyproject.toml</code> solo incluye <code>packages = ['praxeon']</code>. No incluye <code>web/dist</code>.", table_cell_style),
         Paragraph("<b>Fallo crítico:</b> <code>pip install praxeon</code> arroja HTTP 404 en <code>praxeon-web</code>.", table_cell_style)],

        [Paragraph("<b>Dualismo EventBus</b>", table_cell_style),
         Paragraph("Completamente omitido.", table_cell_style),
         Paragraph("Existen 2 clases <code>EventBus</code> incompatibles: <code>telemetry.EventBus</code> y <code>event_bus.EventBus</code>.", table_cell_style),
         Paragraph("<b>Incompatibilidad:</b> <code>Navigator</code> crashea si se conecta al servidor FastAPI.", table_cell_style)],

        [Paragraph("<b>CLI <code>praxeon live</code></b>", table_cell_style),
         Paragraph("Afirmó fallo por BUG-01.", table_cell_style),
         Paragraph("La ayuda documenta <code>--provider simulated</code>, pero el parser exige <code>choices=['simulator']</code>.", table_cell_style),
         Paragraph("<b>Crasheo inmediato:</b> Seguir el <code>--help</code> oficial causa error por opción inválida.", table_cell_style)],

        [Paragraph("<b>Concurrencia</b>", table_cell_style),
         Paragraph("Omitido.", table_cell_style),
         Paragraph("<code>CircuitBreaker</code> muta <code>_state</code> sin ningún <code>threading.Lock</code>.", table_cell_style),
         Paragraph("Condiciones de carrera en peticiones concurrentes en FastAPI.", table_cell_style)],

        [Paragraph("<b>Aislamiento Host</b>", table_cell_style),
         Paragraph("Aprobado por <code>EgressPolicy</code>.", table_cell_style),
         Paragraph("<code>LocalProcessSandbox</code> solo evalúa regex sobre el texto del comando.", table_cell_style),
         Paragraph("<b>Falso aislamiento:</b> Scripts Python tienen salida de red irrestricta en el host.", table_cell_style)],

        [Paragraph("<b>Ejecución OS</b>", table_cell_style),
         Paragraph("Omitido.", table_cell_style),
         Paragraph("Linux usa <code>shlex.split</code> con <code>shell=False</code>; Windows usa PowerShell.", table_cell_style),
         Paragraph("Comandos con tuberías (<code>|</code>) fallan sistemáticamente en Unix.", table_cell_style)],

        [Paragraph("<b>Auth Producción</b>", table_cell_style),
         Paragraph("Señaló omisión de token si falta clave.", table_cell_style),
         Paragraph("<code>app.py</code> valida <code>SECRET_KEY</code> en prod pero ignora <code>API_KEY</code>.", table_cell_style),
         Paragraph("<b>Encadenamiento:</b> Fuerza a usar la clave HMAC de firma como API key pública.", table_cell_style)],
    ]

    t_disc = Table(disc_data, colWidths=[75, 115, 145, 150])
    t_disc.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), PRIMARY),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#D5D8DC")),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, BG_ALT]),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('TOPPADDING', (0,0), (-1,-1), 2.5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2.5),
    ]))
    story.append(t_disc)
    story.append(Spacer(1, 6))

    story.append(Paragraph("2. Análisis Arquitectónico y Estructura del Codebase", h1_style))
    story.append(HRFlowable(width="100%", thickness=1, color=SECONDARY, spaceBefore=1, spaceAfter=6))

    story.append(Paragraph("2.1 Fractura del Sistema de Mensajería (Dual EventBus)", h2_style))
    story.append(Paragraph(
        "El código mantiene dos implementaciones disjuntas con el mismo nombre de clase, provocando un acoplamiento roto:<br/>"
        "• <b><code>praxeon.runtime.telemetry.EventBus</code></b>: Utilizado por <code>Navigator</code>. "
        "Define <code>subscribe(callback)</code> y <code>publish(event: TelemetryEvent)</code> en memoria.<br/>"
        "• <b><code>praxeon.runtime.event_bus.EventBus</code></b>: Utilizado por <code>dependencies.py</code> y <code>proxy_middleware.py</code>. "
        "Define <code>emit(session_id, event_type, ...)</code>, integra persistencia transaccional SQLite (<code>EventStore</code>) "
        "y colas asíncronas para WebSockets/SSE.<br/>"
        "<b>Consecuencia:</b> Es imposible inyectar el runtime formal <code>Navigator</code> en la API web sin disparar "
        "un <code>AttributeError: 'EventBus' object has no attribute 'publish'</code>.",
        body_style
    ))

    story.append(Paragraph("2.2 Dualismo en Modelos de Dominio (Legacy vs Modern)", h2_style))
    story.append(Paragraph(
        "Se constata la coexistencia de dos contratos de datos incompatibles para la entidad fundamental del agente: "
        "<code>models/schema.py</code> (mutable, campos <code>tool_name</code>/<code>tool_args</code>) frente a "
        "<code>domain/action.py</code> (inmutable con <code>frozen=True</code> y objeto <code>ToolCall</code>). "
        "Para intercomunicarlos en <code>core/jev_engine.py</code>, se recurre a la instanciación de clases dinámicas "
        "en tiempo de ejecución (<code>type('MockStep', (), ...)</code>), introduciendo fragilidad estructural.",
        body_style
    ))

    story.append(Paragraph("2.3 Bypass de Autorización en JEVProxyMiddleware", h2_style))
    story.append(Paragraph(
        "En <code>praxeon/interceptor/proxy_middleware.py</code> (L449-L463), el middleware auto-firma capacidades con "
        "estado <code>DecisionStatus.ALLOW</code> incondicional, <b>puenteando por completo a <code>PolicyEngine</code> y <code>RiskEngine</code></b>. "
        "Cualquier herramienta invocada por el agente es aprobada físicamente sin verificar políticas de seguridad.",
        body_style
    ))

    # SALTO A PÁGINA 3
    story.append(PageBreak())

    # =========================================================================
    # PÁGINA 3: VULNERABILIDADES CRÍTICAS DE SEGURIDAD
    # =========================================================================
    story.append(Paragraph("3. Vulnerabilidades Críticas de Seguridad", h1_style))
    story.append(HRFlowable(width="100%", thickness=1, color=SECONDARY, spaceBefore=1, spaceAfter=8))

    story.append(Paragraph("<font color='#C0392B'><b>[CRÍTICA - CVSS 9.8]</b></font> <b>VULN-01: Bypass de Autenticación 'Fail-Open' en Producción</b>", h2_style))
    v1_text = """
    <b>Archivos afectados:</b> <code>praxeon/server/dependencies.py</code> (L2142-L2171), <code>websocket.py</code> (L115-L118) y <code>app.py</code> (L23-L30).<br/>
    <b>Mecanismo de Vulnerabilidad:</b><br/>
    En <code>verify_api_key</code>, la lógica de validación opera de la siguiente forma:
    """
    story.append(Paragraph(v1_text, body_style))
    story.append(Paragraph(
        'expected_key = os.environ.get("PRAXEON_API_KEY")\n'
        'if is_auth_required():\n'
        '    if not token:\n'
        '        raise HTTPException(401, "Missing API Key")\n'
        '    if expected_key and token != expected_key:  # <-- SI expected_key ES None, ESTO ES FALSO\n'
        '        raise HTTPException(401, "Invalid API Key")\n'
        '    return token  # <-- RETORNA ÉXITO CON CUALQUIER TOKEN SUMINISTRADO POR EL ATACANTE',
        code_style
    ))
    story.append(Paragraph(
        "<b>Encadenamiento Peligroso:</b> En <code>app.py</code>, la comprobación de arranque en entorno de producción "
        "verifica estrictamente que <code>PRAXEON_SECRET_KEY</code> esté configurado y supere los 32 caracteres, pero "
        "<b>omite comprobar <code>PRAXEON_API_KEY</code></b>. Esto provoca que un despliegue productivo estándar arranque "
        "con autenticación requerida pero sin clave de API configurada, aceptando cualquier petición externa sin autenticación.",
        body_style
    ))
    story.append(Spacer(1, 4))

    story.append(Paragraph("<font color='#E67E22'><b>[ALTA - CVSS 7.5]</b></font> <b>VULN-02: Ataque de Temporización (Timing Attack) en Validación de Tokens</b>", h2_style))
    story.append(Paragraph(
        "<b>Archivos afectados:</b> <code>dependencies.py</code> (L2166) y <code>websocket.py</code> (L116).<br/>"
        "La comparación de tokens de API y tokens WebSocket se realiza mediante el operador de igualdad estándar de Python "
        "<code>token != expected_key</code>. Esta operación finaliza en el primer byte discordante, permitiendo a un atacante "
        "remoto inferir la clave válida carácter por carácter midiendo variaciones microscópicas de latencia. "
        "Debe sustituirse inmediatamente por <code>secrets.compare_digest</code>.",
        body_style
    ))
    story.append(Spacer(1, 4))

    story.append(Paragraph("<font color='#E67E22'><b>[ALTA - CVSS 7.2]</b></font> <b>VULN-03: Falsa Contención de Red en LocalProcessSandbox</b>", h2_style))
    story.append(Paragraph(
        "<b>Archivos afectados:</b> <code>praxeon/runtime/sandbox.py</code> (L230-L260) y <code>policy/egress.py</code> (L110-L157).<br/>"
        "A diferencia de <code>ContainerSandboxAdapter</code>, la clase <code>LocalProcessSandbox</code> no crea namespaces de red "
        "ni reglas de firewall a nivel de sistema operativo. Su verificación se limita a ejecutar una expresión regular sobre "
        "el comando recibido buscando utilidades como <code>curl</code> o <code>wget</code>. "
        "Cualquier script en Python o comando compilado ejecutado mediante <code>run_command</code> que abra sockets directamente "
        "goza de conectividad completa hacia la red local o Internet sin restricción alguna.",
        body_style
    ))
    story.append(Spacer(1, 4))

    story.append(Paragraph("<font color='#D4AC0D'><b>[MEDIA - CVSS 5.3]</b></font> <b>VULN-04: Configuración Insegura de CORS con Credenciales</b>", h2_style))
    story.append(Paragraph(
        "<b>Archivos afectados:</b> <code>praxeon/server/app.py</code> (L51, L70).<br/>"
        "La aplicación configura <code>allow_origins=['*']</code> simultáneamente con <code>allow_credentials=True</code>. "
        "Esto viola la especificación del W3C y fuerza a los navegadores modernos a rechazar peticiones o a que el middleware "
        "refleje el origen de cualquier sitio web solicitante, facilitando ataques de Cross-Site Scripting y secuestro de sesiones.",
        body_style
    ))

    # SALTO A PÁGINA 4
    story.append(PageBreak())

    # =========================================================================
    # PÁGINA 4: ERRORES FUNCIONALES Y RUPTURAS DE RUNTIME
    # =========================================================================
    story.append(Paragraph("4. Errores Funcionales y Rupturas de Runtime", h1_style))
    story.append(HRFlowable(width="100%", thickness=1, color=SECONDARY, spaceBefore=1, spaceAfter=8))

    story.append(Paragraph("<font color='#C0392B'><b>[BUG CRÍTICO]</b></font> <b>BUG-01: Bloqueo Sistemático del Agente Multi-Paso por Falso Replay Attack</b>", h2_style))
    story.append(Paragraph(
        "<b>Archivo:</b> <code>praxeon/interceptor/proxy_middleware.py</code> (L433-L435).<br/>"
        "<b>Causa Raíz Comprobada:</b> En el método <code>execute_tool</code>, el identificador de la acción se calcula como:<br/>"
        "<code>step_idx = len(self.session_state.steps)</code> &nbsp;|&nbsp; <code>action_id = f'act_{step_idx}'</code><br/>"
        "Sin embargo, <code>self.session_state.steps</code> <b>nunca se incrementa ni se actualiza</b> tras la ejecución física de herramientas. "
        "En el Turno 1 se genera <code>act_0</code> y su correspondiente capability <code>dec_act_0</code>, que es registrado en <code>NonceStore</code>. "
        "En el Turno 2, al ser <code>len(steps) == 0</code>, vuelve a generar exactamente <code>dec_act_0</code>. "
        "Al consultar el <code>NonceStore</code>, <code>SecureExecutor</code> deniega inmediatamente la ejecución con:<br/>"
        "<i>'PolicyViolation: El capability dec_act_0 ya ha sido consumido previamente (Replay attack prevention)'</i>.<br/>"
        "<b>Impacto:</b> Ningún agente autónomo puede ejecutar más de una herramienta en toda su sesión sin congelarse.",
        body_style
    ))
    story.append(Spacer(1, 4))

    story.append(Paragraph("<font color='#E67E22'><b>[BUG ALTO]</b></font> <b>BUG-02: Crasheo Inmediato de la Demo Oficial (demo.py)</b>", h2_style))
    story.append(Paragraph(
        "<b>Archivos:</b> <code>examples/demo_offline.py</code> (L107-L121) y <code>praxeon/providers/laya.py</code> (L250-L253).<br/>"
        "<code>LayaProvider</code> verifica mediante <code>os.path.exists</code> la existencia física en disco del archivo <code>src/main.py</code>. "
        "Al no existir en el entorno de prueba, asigna <code>is_grounded = 0.05</code>, provocando que <code>PolicyEngine</code> "
        "emita un recibo de <code>REPLAN</code>. La demo no valida el recibo e invoca <code>SecureExecutor.execute</code> directamente, "
        "abortando con código de salida 1 por <code>PolicyViolation</code>. La demo de bienvenida oficial no funciona tal cual se distribuye.",
        body_style
    ))
    story.append(Spacer(1, 4))

    story.append(Paragraph("<font color='#C0392B'><b>[BUG CRÍTICO]</b></font> <b>BUG-03: praxeon-web Distribuye una SPA Inexistente en Wheels (HTTP 404)</b>", h2_style))
    story.append(Paragraph(
        "<b>Archivos:</b> <code>pyproject.toml</code> (L55-L57) y <code>praxeon/server/app.py</code> (L80-L83).<br/>"
        "La configuración de <code>setuptools</code> en <code>pyproject.toml</code> declara únicamente <code>packages = ['praxeon']</code>. "
        "Los archivos compilados del dashboard en <code>web/dist/</code> no forman parte del paquete Python ni se incluyen en el <code>.whl</code>. "
        "Cuando un usuario instala el paquete con <code>pip install praxeon</code> y ejecuta <code>praxeon-web</code>, el servidor FastAPI "
        "busca <code>../../web/dist</code> fuera del directorio del paquete instalado en <code>site-packages</code>. "
        "Al no encontrar los archivos, la ruta raíz <code>/</code> devuelve un error <b>404 Not Found</b>.",
        body_style
    ))
    story.append(Spacer(1, 4))

    story.append(Paragraph("<font color='#E67E22'><b>[BUG ALTO]</b></font> <b>BUG-04: Código Muerto y Bypass de Detección de Herramientas Dinámicas</b>", h2_style))
    story.append(Paragraph(
        "<b>Archivo:</b> <code>praxeon/policy/registry.py</code> (L58-L62).<br/>"
        "El método de registro dinámico contiene:<br/>"
        "<code>if re.match(r'^[a-zA-Z0-9_\-\.]+$', clean):</code><br/>"
        "<code>    if shutil.which(clean) is not None: return True</code><br/>"
        "<code>    return True  # <-- ANULA LA VALIDACIÓN DE EXISTENCIA REAL DEL SISTEMA</code><br/>"
        "Cualquier comando inexistente es registrado dinámicamente como herramienta válida de riesgo medio, burlándose la verificación del SO.",
        body_style
    ))
    story.append(Spacer(1, 4))

    story.append(Paragraph("<font color='#D4AC0D'><b>[BUG MEDIO]</b></font> <b>BUG-05: Asimetría de Ejecución Shell Unix vs Windows</b>", h2_style))
    story.append(Paragraph(
        "<b>Archivo:</b> <code>praxeon/runtime/sandbox.py</code> (L233-L260).<br/>"
        "En Linux/macOS, <code>LocalProcessSandbox</code> invoca <code>subprocess.run(shlex.split(cmd_str), shell=False)</code>. "
        "Cualquier comando que contenga tuberías (<code>|</code>), redirecciones (<code>></code>) o encadenamientos (<code>&&</code>) "
        "falla inmediatamente con error de sintaxis porque los operadores de shell se pasan como argumentos literales al binario.",
        body_style
    ))

    # SALTO A PÁGINA 5
    story.append(PageBreak())

    # =========================================================================
    # PÁGINA 5: PRUEBAS, EMPAQUETADO Y PLAN DE REMEDIACIÓN
    # =========================================================================
    story.append(Paragraph("5. Auditoría de Pruebas, Empaquetado y Dependencias", h1_style))
    story.append(HRFlowable(width="100%", thickness=1, color=SECONDARY, spaceBefore=1, spaceAfter=6))

    story.append(Paragraph(
        "• <b>Puntos Ciegos en Tests (284 tests):</b> Ninguna prueba ejecuta más de una herramienta consecutiva en <code>JEVProxyMiddleware</code> (ocultó BUG-01). "
        "Las pruebas de auth siempre suministran claves válidas, omitiendo el escenario sin clave (VULN-01). "
        "<code>test_dod_1_wheel_packaging_cleanliness</code> falla en clones limpios si no se ejecuta previamente <code>build</code>.<br/>"
        "• <b>Dependencias Base en pyproject.toml:</b> <code>fastapi</code>, <code>uvicorn</code> y <code>websockets</code> están indebidamente clasificadas "
        "como extras opcionales, provocando <code>ModuleNotFoundError</code> al ejecutar los CLI oficiales.<br/>"
        "• <b>Deprecaciones:</b> <b>46 ocurrencias de <code>datetime.utcnow()</code></b> en el código (deprecado en Python 3.12+).<br/>"
        "• <b>Frontend Web (React/Vite):</b> Compila en 878ms pero acumula <b>87 advertencias de linter Oxlint</b> (llamadas síncronas a <code>setState</code> en efectos).",
        body_style
    ))
    story.append(Spacer(1, 4))

    story.append(Paragraph("6. Matriz Priorizada de Remediación y Plan de Acción", h1_style))
    story.append(HRFlowable(width="100%", thickness=1, color=SECONDARY, spaceBefore=1, spaceAfter=6))

    rem_data = [
        [Paragraph("Prio", table_header_style), Paragraph("ID", table_header_style), Paragraph("Módulo / Archivo", table_header_style), Paragraph("Acción Técnica Requerida", table_header_style)],
        
        [Paragraph("<font color='#C0392B'><b>P0</b></font>", table_cell_bold),
         Paragraph("<b>VULN-01</b>", table_cell_style),
         Paragraph("<code>server/dependencies.py</code><br/><code>server/app.py</code>", table_cell_style),
         Paragraph("Implementar <b>Fail-Closed</b> estricto: rechazar con HTTP 401 si <code>auth_required</code> es True y la clave no está definida. Usar <code>secrets.compare_digest</code>.", table_cell_style)],

        [Paragraph("<font color='#C0392B'><b>P0</b></font>", table_cell_bold),
         Paragraph("<b>BUG-01</b>", table_cell_style),
         Paragraph("<code>interceptor/proxy_middleware.py</code>", table_cell_style),
         Paragraph("Invocar <code>self.session_state.add_step(...)</code> tras la ejecución física para que el índice de pasos se incremente y evolucione el hash del estado.", table_cell_style)],

        [Paragraph("<font color='#E67E22'><b>P1</b></font>", table_cell_bold),
         Paragraph("<b>BUG-03</b>", table_cell_style),
         Paragraph("<code>pyproject.toml</code><br/><code>server/app.py</code>", table_cell_style),
         Paragraph("Mover <code>web/dist</code> a <code>praxeon/server/static/</code>, registrarlo en el paquete wheel y servirlo con <code>importlib.resources</code>.", table_cell_style)],

        [Paragraph("<font color='#E67E22'><b>P1</b></font>", table_cell_bold),
         Paragraph("<b>Deps Core</b>", table_cell_style),
         Paragraph("<code>pyproject.toml</code>", table_cell_style),
         Paragraph("Mover <code>fastapi</code>, <code>uvicorn</code> y <code>websockets</code> de dependencias opcionales a obligatorias en el paquete base.", table_cell_style)],

        [Paragraph("<font color='#E67E22'><b>P1</b></font>", table_cell_bold),
         Paragraph("<b>BUG-02</b>", table_cell_style),
         Paragraph("<code>examples/demo_offline.py</code>", table_cell_style),
         Paragraph("Verificar el estado del recibo devuelto por <code>PolicyEngine</code> antes de invocar a <code>SecureExecutor</code> para evitar <code>PolicyViolation</code>.", table_cell_style)],

        [Paragraph("<font color='#2E5B88'><b>P2</b></font>", table_cell_bold),
         Paragraph("<b>Dual Bus</b>", table_cell_style),
         Paragraph("<code>runtime/event_bus.py</code><br/><code>runtime/telemetry.py</code>", table_cell_style),
         Paragraph("Construir una fachada unificadora para que <code>Navigator</code> pueda emitir eventos directamente al bus persistente SQLite.", table_cell_style)],

        [Paragraph("<font color='#2E5B88'><b>P2</b></font>", table_cell_bold),
         Paragraph("<b>Concurrencia</b>", table_cell_style),
         Paragraph("<code>providers/resilience.py</code>", table_cell_style),
         Paragraph("Introducir <code>threading.Lock()</code> en <code>CircuitBreaker</code> para proteger la mutación concurrente de estados y contadores de fallos.", table_cell_style)],

        [Paragraph("<font color='#2E5B88'><b>P2</b></font>", table_cell_bold),
         Paragraph("<b>BUG-04</b>", table_cell_style),
         Paragraph("<code>policy/registry.py</code>", table_cell_style),
         Paragraph("Eliminar el <code>return True</code> incondicional para respetar el filtrado real de binarios ejecutables con <code>shutil.which</code>.", table_cell_style)],
    ]

    t_rem = Table(rem_data, colWidths=[30, 65, 125, 265])
    t_rem.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), PRIMARY),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#D5D8DC")),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, BG_ALT]),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('TOPPADDING', (0,0), (-1,-1), 2.5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2.5),
    ]))
    story.append(t_rem)
    story.append(Spacer(1, 10))

    conclusion_html = """
    <b>DICTAMEN FINAL Y CONCLUSIÓN DE LA AUDITORÍA:</b><br/>
    Praxeon posee un diseño matemático y conceptual sobresaliente en supervisión de agentes autónomos. La separación entre 
    planificación y ejecución física mediante capabilities criptográficos es un modelo de referencia. Una vez aplicadas las correcciones 
    P0 y P1 detalladas en este informe (estimadas en menos de 2 jornadas de desarrollo de un ingeniero senior), el software alcanzará 
    la robustez, resiliencia y seguridad requeridas para su certificación y despliegue productivo oficial v1.0.0.
    """
    conclusion_table = Table(
        [[Paragraph(conclusion_html, callout_success_style)]],
        colWidths=[485]
    )
    conclusion_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#EAFAF1")),
        ('BOX', (0,0), (-1,-1), 1.2, SUCCESS),
        ('PADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(conclusion_table)

    # Construir documento
    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"Informe PDF generado con exito: {output_filename}")

if __name__ == "__main__":
    output_pdf = sys.argv[1] if len(sys.argv) > 1 else "PRAXEON_AUDITORIA_INTEGRAL_v1.0.0.pdf"
    build_audit_pdf(output_pdf)
