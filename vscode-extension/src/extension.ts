import * as vscode from 'vscode';

const API_URL_KEY = 'codey.apiUrl';
const API_KEY_KEY = 'codey.apiKey';

// Gutter decoration types for health indicators
const healthyDecoration = vscode.window.createTextEditorDecorationType({
  gutterIconPath: vscode.Uri.parse('data:image/svg+xml,' + encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12"><circle cx="6" cy="6" r="4" fill="#22c55e"/></svg>'
  )),
  gutterIconSize: '80%',
});

const cautionDecoration = vscode.window.createTextEditorDecorationType({
  gutterIconPath: vscode.Uri.parse('data:image/svg+xml,' + encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12"><circle cx="6" cy="6" r="4" fill="#eab308"/></svg>'
  )),
  gutterIconSize: '80%',
});

const criticalDecoration = vscode.window.createTextEditorDecorationType({
  gutterIconPath: vscode.Uri.parse('data:image/svg+xml,' + encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12"><circle cx="6" cy="6" r="4" fill="#ef4444"/></svg>'
  )),
  gutterIconSize: '80%',
});

export function activate(context: vscode.ExtensionContext) {
  console.log('Codey extension activated');

  // Register commands
  context.subscriptions.push(
    vscode.commands.registerCommand('codey.askAboutFile', askAboutFile),
    vscode.commands.registerCommand('codey.generateCode', generateCode),
    vscode.commands.registerCommand('codey.analyzeHealth', analyzeHealth),
    vscode.commands.registerCommand('codey.showHealthPanel', showHealthPanel),
  );

  // Status bar item showing health
  const statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBar.text = '$(pulse) Codey';
  statusBar.tooltip = 'Codey Structural Health';
  statusBar.command = 'codey.showHealthPanel';
  statusBar.show();
  context.subscriptions.push(statusBar);
}

async function callApi(endpoint: string, body?: object): Promise<any> {
  const config = vscode.workspace.getConfiguration();
  const apiUrl = config.get<string>(API_URL_KEY) || 'https://codey-jc2r.onrender.com';
  const apiKey = config.get<string>(API_KEY_KEY) || '';

  if (!apiKey) {
    vscode.window.showWarningMessage('Codey: Set your API key in Settings (codey.apiKey)');
    return null;
  }

  const url = `${apiUrl}${endpoint}`;
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${apiKey}`,
  };

  try {
    const resp = await fetch(url, {
      method: body ? 'POST' : 'GET',
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: 'Request failed' }));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }

    return await resp.json();
  } catch (e: any) {
    vscode.window.showErrorMessage(`Codey: ${e.message}`);
    return null;
  }
}

async function askAboutFile() {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    vscode.window.showWarningMessage('No active file');
    return;
  }

  const question = await vscode.window.showInputBox({
    prompt: 'Ask Codey about this file',
    placeHolder: 'What does this function do? / Find bugs / Suggest improvements',
  });

  if (!question) return;

  const code = editor.document.getText();
  const filename = editor.document.fileName;

  const result = await callApi('/sessions/prompt', {
    prompt: `About the file ${filename}:\n\n${question}\n\nCode:\n\`\`\`\n${code.substring(0, 8000)}\n\`\`\``,
    language: editor.document.languageId,
  });

  if (result?.output) {
    const doc = await vscode.workspace.openTextDocument({
      content: result.output,
      language: 'markdown',
    });
    vscode.window.showTextDocument(doc, { viewColumn: vscode.ViewColumn.Beside });
  }
}

async function generateCode() {
  const prompt = await vscode.window.showInputBox({
    prompt: 'What do you want Codey to generate?',
    placeHolder: 'Build a REST API endpoint for user authentication with JWT',
  });

  if (!prompt) return;

  vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: 'Codey: Generating...' },
    async () => {
      const result = await callApi('/sessions/prompt', { prompt });

      if (result?.output) {
        const editor = vscode.window.activeTextEditor;
        if (editor) {
          editor.edit(editBuilder => {
            editBuilder.insert(editor.selection.active, result.output);
          });
        } else {
          const doc = await vscode.workspace.openTextDocument({ content: result.output });
          vscode.window.showTextDocument(doc);
        }

        if (result.health) {
          vscode.window.showInformationMessage(
            `Codey: ${result.lines_generated} lines | Health: ${result.health.phase} | ${result.estimated_credits} credit(s)`
          );
        }
      }
    }
  );
}

async function analyzeHealth() {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    vscode.window.showWarningMessage('No active file');
    return;
  }

  vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: 'Codey: Analyzing health...' },
    async () => {
      const code = editor.document.getText();
      const result = await callApi('/analyze/code', {
        code,
        filename: editor.document.fileName,
        language: editor.document.languageId,
      });

      if (result?.report) {
        const r = result.report;
        const panel = vscode.window.createWebviewPanel(
          'codeyHealth',
          `Health: ${r.phase}`,
          vscode.ViewColumn.Beside,
          {}
        );

        panel.webview.html = `
          <html>
          <body style="font-family: system-ui; padding: 20px; background: #0a0a0a; color: #f1f5f9;">
            <h2 style="color: ${r.phase === 'Excellent' ? '#22c55e' : r.phase === 'Watch this' ? '#eab308' : '#ef4444'}">
              ${r.phase}
            </h2>
            <p>Health Score: ${(r.health_score * 100).toFixed(0)}%</p>
            <p>Components: ${r.total_nodes} | Dependencies: ${r.total_edges}</p>
            <p>Coherence: ${(r.coherence * 100).toFixed(0)}% | Stability: ${(r.stability * 100).toFixed(0)}%</p>
            <h3>Summary</h3>
            <p>${r.summary}</p>
            <h3>Recommendations</h3>
            <ul>${(result.recommendations || []).map((r: string) => `<li>${r}</li>`).join('')}</ul>
          </body>
          </html>
        `;
      }
    }
  );
}

async function showHealthPanel() {
  vscode.window.showInformationMessage('Codey health panel — use "Codey: Analyze structural health" on an open file');
}

export function deactivate() {}
