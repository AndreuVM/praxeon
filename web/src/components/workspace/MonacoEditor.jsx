import React from 'react';
import * as monaco from 'monaco-editor';
import Editor, { loader } from '@monaco-editor/react';
import editorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker';
import jsonWorker from 'monaco-editor/esm/vs/language/json/json.worker?worker';
import cssWorker from 'monaco-editor/esm/vs/language/css/css.worker?worker';
import htmlWorker from 'monaco-editor/esm/vs/language/html/html.worker?worker';
import tsWorker from 'monaco-editor/esm/vs/language/typescript/ts.worker?worker';

// Monaco (el motor de VS Code) empaquetado localmente: sin CDN, funciona sin conexión en la app de escritorio.
self.MonacoEnvironment = {
  getWorker(_, label) {
    if (label === 'json') return new jsonWorker();
    if (label === 'css' || label === 'scss' || label === 'less') return new cssWorker();
    if (label === 'html' || label === 'handlebars' || label === 'razor') return new htmlWorker();
    if (label === 'typescript' || label === 'javascript') return new tsWorker();
    return new editorWorker();
  },
};
loader.config({ monaco });

export default function MonacoEditor({ path, language, value, onChange, onSave }) {
  return (
    <Editor
      height="100%"
      path={path}
      language={language}
      value={value}
      theme="vs-dark"
      onChange={(v) => onChange?.(v ?? '')}
      onMount={(editor, m) => {
        editor.addCommand(m.KeyMod.CtrlCmd | m.KeyCode.KeyS, () => onSave?.());
      }}
      options={{ automaticLayout: true, minimap: { enabled: true }, fontSize: 13, scrollBeyondLastLine: false, tabSize: 2 }}
    />
  );
}
