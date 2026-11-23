import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "../App";
import { S } from "../strings.pt";
import { mockApi } from "../test/mockApi";

const ME = { "GET /auth/me": { user: "admin" } };

const evidence = [
  { kind: "log", ref: "log:api-7d9", summary: "OOMKilled no container api" },
  { kind: "commit", ref: "commit:abc123", summary: "deploy aumentou o limite de memória" },
];

const incident = (over: Record<string, unknown> = {}) => ({
  id: 42,
  fingerprint: "f",
  alert_name: "HighMemory",
  labels: { service: "api" },
  fired_at: "2026-11-23T14:03:00Z",
  created_at: "2026-11-23T14:03:01Z",
  status: "done",
  error: null,
  approvals: [],
  approvable: true,
  rca: {
    cause: "deploy 14:03 aumentou memória do pod api",
    confidence: 0.82,
    evidence,
    suggested_command: "kubectl rollout undo deploy/api -n prod",
    unknown: false,
  },
  ...over,
});

function open(id: number, routes: Record<string, unknown>) {
  const api = mockApi({ ...ME, "GET /incidents": [incident()], ...routes });
  window.location.hash = `#/incidentes/${id}`;
  render(<App />);
  return api;
}

test("Precisao mostra rotulo cenarios sinteticos e o modelo", async () => {
  mockApi({
    ...ME,
    "GET /scoreboard": {
      measured: true, correct: 47, total: 52, model: "anthropic:claude-sonnet-5-5",
      injection_errors: 0, label: "cenários sintéticos",
      calibration: [{ confidence: 0.9, hit_rate: 0.8 }],
    },
  });
  window.location.hash = "#/precisao";
  render(<App />);
  expect(await screen.findByText("cenários sintéticos")).toBeInTheDocument();
  expect(screen.getByText("anthropic:claude-sonnet-5-5")).toBeInTheDocument();
  expect(screen.getByText("47/52")).toBeInTheDocument();
  expect(screen.getByText(/80%/)).toBeInTheDocument(); // calibração
});

test("Precisao mostra cenarios nao mediveis com motivo e a procedencia do placar", async () => {
  mockApi({
    ...ME,
    "GET /scoreboard": {
      measured: true, correct: 1, total: 4, model: "m", injection_errors: 0, label: "cenários sintéticos",
      calibration: [], timestamp: "2026-11-23T12:00:00+00:00", git_commit: "abcdef1234567890",
      enabled_tools: ["kubernetes"], not_measurable: 1,
      scenarios: [
        { scenario_id: "oom-kill", correct: true, confidence: 0.9, measurable: true, reason: null },
        { scenario_id: "dns-failure", correct: false, confidence: 0, measurable: false, reason: "não medível neste ambiente: requer uma destas ferramentas habilitadas: loki" },
      ],
    },
  });
  window.location.hash = "#/precisao";
  render(<App />);
  expect(await screen.findByText("1/4")).toBeInTheDocument();
  expect(screen.getByText(S.precisao.naoMediveis(1))).toBeInTheDocument();
  expect(screen.getByText(/dns-failure: não medível neste ambiente/)).toBeInTheDocument();
  expect(screen.getByText("abcdef123456")).toBeInTheDocument();
  expect(screen.getByText("kubernetes")).toBeInTheDocument();
});

test("Precisao sempre mostra o rotulo fixo de cenarios sinteticos, nunca o do arquivo", async () => {
  mockApi({
    ...ME,
    "GET /scoreboard": { measured: true, correct: 7, total: 7, model: "m", injection_errors: 0, label: "produção real", calibration: [] },
  });
  window.location.hash = "#/precisao";
  render(<App />);
  expect(await screen.findByText("cenários sintéticos")).toBeInTheDocument();
  expect(screen.queryByText("produção real")).not.toBeInTheDocument();
});

test("Precisao sem medicao mostra 'sem medicao ainda' e nenhum numero", async () => {
  mockApi({ ...ME, "GET /scoreboard": { measured: false, reason: "missing" } });
  window.location.hash = "#/precisao";
  render(<App />);
  expect(await screen.findByText(S.precisao.semMedicao)).toBeInTheDocument();
  expect(screen.queryByText(/\d+\/\d+/)).not.toBeInTheDocument();
});

test("Incidente mostra evidencias e confianca", async () => {
  const user = userEvent.setup();
  open(42, { "GET /incidents/42": incident() });
  expect(await screen.findByText("deploy 14:03 aumentou memória do pod api")).toBeInTheDocument();
  expect(screen.getByText(/Confiança 82%/)).toBeInTheDocument();
  const refBtn = screen.getByRole("button", { name: /log:api-7d9/ });
  expect(screen.getByRole("button", { name: /commit:abc123/ })).toBeInTheDocument();
  expect(screen.queryByText("OOMKilled no container api")).not.toBeInTheDocument();
  await user.click(refBtn);
  expect(screen.getByText("OOMKilled no container api")).toBeInTheDocument();
  expect(screen.getByText("kubectl rollout undo deploy/api -n prod")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: S.incidente.aprovar })).toBeInTheDocument();
});

test('Incidente desconhecido mostra "nao sei" e as evidencias coletadas', async () => {
  const unknown = incident({
    approvable: false,
    rca: { cause: null, confidence: 0.1, evidence, suggested_command: null, unknown: true },
  });
  open(42, { "GET /incidents/42": unknown });
  expect(await screen.findByText(S.incidente.naoSei)).toBeInTheDocument();
  expect(screen.getByText(S.incidente.evidenciasColetadas)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /log:api-7d9/ })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: S.incidente.aprovar })).not.toBeInTheDocument();
});

test("nao ha botao de aprovar para RCA sem comando, comando nao exibivel ou aprovavel=false", async () => {
  for (const [approvable, command] of [[true, null], [false, "echo `id`"]] as const) {
    const inc = incident({
      approvable,
      rca: { cause: "x", confidence: 0.6, evidence, suggested_command: command, unknown: false },
    });
    mockApi({ ...ME, "GET /incidents": [inc], "GET /incidents/42": inc });
    window.location.hash = "#/incidentes/42";
    const { unmount } = render(<App />);
    await screen.findByText(/Confiança 60%/);
    expect(screen.queryByRole("button", { name: S.incidente.aprovar })).not.toBeInTheDocument();
    unmount();
  }
});

test("comando sugerido mostra aviso de IA e marca comandos fora da lista de leitura", async () => {
  open(42, { "GET /incidents/42": incident({ command_read_only: false }) });
  expect(await screen.findByText(S.incidente.comandoGeradoPorIa)).toBeInTheDocument();
  expect(S.incidente.comandoGeradoPorIa).toMatch(/gerado por IA a partir de dados não confiáveis; revise antes de executar manualmente/);
  expect(screen.getByText(S.incidente.comandoForaListaLeitura)).toBeInTheDocument();
});

test("comando somente leitura mostra aviso de IA sem o aviso de fora da lista", async () => {
  const inc = incident({
    command_read_only: true,
    rca: { cause: "x", confidence: 0.6, evidence, suggested_command: "kubectl get pods -n prod", unknown: false },
  });
  open(42, { "GET /incidents/42": inc });
  expect(await screen.findByText(S.incidente.comandoGeradoPorIa)).toBeInTheDocument();
  expect(screen.queryByText(S.incidente.comandoForaListaLeitura)).not.toBeInTheDocument();
});

test("aprovar chama o endpoint sem enviar comando no corpo", async () => {
  const user = userEvent.setup();
  let approved = false;
  const api = open(42, {
    "GET /incidents/42": () => ({
      body: incident({ approvals: approved ? [{ user_id: "web:admin", command: "kubectl rollout undo deploy/api -n prod" }] : [] }),
    }),
    "POST /incidents/42/approve": () => {
      approved = true;
      return { body: { ok: true } };
    },
  });
  await user.click(await screen.findByRole("button", { name: S.incidente.aprovar }));
  await waitFor(() => expect(screen.getByText(S.incidente.nadaExecutado)).toBeInTheDocument());
  const post = api.calls.find((c) => c.method === "POST")!;
  expect(post.init?.body).toBeUndefined();
  expect(screen.queryByRole("button", { name: S.incidente.aprovar })).not.toBeInTheDocument();
});

test("texto do LLM e renderizado como texto puro", async () => {
  const hostile = '<img src=x onerror="alert(1)"><script>alert(2)</script>';
  const inc = incident({
    rca: { cause: hostile, confidence: 0.5, evidence: [{ kind: "log", ref: "log:x", summary: hostile }], suggested_command: null, unknown: false },
    approvable: false,
  });
  open(42, { "GET /incidents/42": inc });
  expect(await screen.findByText(hostile)).toBeInTheDocument();
  expect(document.querySelector("img")).toBeNull();
  expect(document.querySelector("script")).toBeNull();
});

test("tela de login aparece quando a API responde 401", async () => {
  mockApi({ "GET /auth/me": () => ({ status: 401, body: { detail: "Não autenticado." } }) });
  render(<App />);
  expect(await screen.findByRole("button", { name: S.login.entrar })).toBeInTheDocument();
  expect(screen.queryByText(S.nav.incidentes)).not.toBeInTheDocument();
});

test("401 no meio do uso leva de volta ao login", async () => {
  mockApi({ ...ME, "GET /incidents": () => ({ status: 401, body: { detail: "x" } }) });
  render(<App />);
  expect(await screen.findByRole("button", { name: S.login.entrar })).toBeInTheDocument();
});

test("login envia usuario e senha e entra no app", async () => {
  const user = userEvent.setup();
  let logged = false;
  const api = mockApi({
    "GET /auth/me": () => (logged ? { body: { user: "admin" } } : { status: 401, body: {} }),
    "POST /auth/login": () => {
      logged = true;
      return { body: { user: "admin" } };
    },
    "GET /incidents": [],
  });
  render(<App />);
  await user.type(await screen.findByLabelText(S.login.usuario), "admin");
  await user.type(screen.getByLabelText(S.login.senha), "segredo");
  await user.click(screen.getByRole("button", { name: S.login.entrar }));
  expect(await screen.findByText(S.incidentes.vazio)).toBeInTheDocument();
  const post = api.calls.find((c) => c.path === "/auth/login")!;
  expect(JSON.parse(String(post.init?.body))).toEqual({ username: "admin", password: "segredo" });
});

test("login mostra mensagem do servidor quando nao configurado", async () => {
  const user = userEvent.setup();
  mockApi({
    "GET /auth/me": () => ({ status: 401, body: {} }),
    "POST /auth/login": () => ({ status: 503, body: { detail: "Login não configurado: defina X." } }),
  });
  render(<App />);
  await user.type(await screen.findByLabelText(S.login.usuario), "a");
  await user.type(screen.getByLabelText(S.login.senha), "b");
  await user.click(screen.getByRole("button", { name: S.login.entrar }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Login não configurado");
});

test("Risco lista workloads com estado, score e limiar", async () => {
  mockApi({
    ...ME,
    "GET /risk": [
      { workload: "prod/api", state: "ativo", score: 0.71, threshold: 0.65 },
      { workload: "prod/web", state: "aprendendo", score: 0, threshold: 0.65 },
    ],
  });
  window.location.hash = "#/risco";
  render(<App />);
  const row = (await screen.findByText("prod/api")).closest("tr")!;
  expect(within(row).getByText(S.risco.estado.ativo)).toBeInTheDocument();
  expect(within(row).getByText(/0,71/)).toBeInTheDocument();
  const learning = screen.getByText("prod/web").closest("tr")!;
  expect(within(learning).getByText(S.risco.estado.aprendendo)).toBeInTheDocument();
});

test("Risco vazio explica que nada esta configurado", async () => {
  mockApi({ ...ME, "GET /risk": [] });
  window.location.hash = "#/risco";
  render(<App />);
  expect(await screen.findByText(S.risco.vazio)).toBeInTheDocument();
});

test("Integracoes mostra status sem valores de segredo", async () => {
  mockApi({
    ...ME,
    "GET /integrations": [
      { id: "slack", configured: true, detail: null },
      { id: "loki", configured: false, detail: null },
    ],
  });
  window.location.hash = "#/integracoes";
  render(<App />);
  const slack = (await screen.findByText(S.integracoes.nomes.slack)).closest("li")!;
  expect(within(slack).getByText(S.integracoes.configurada)).toBeInTheDocument();
  const loki = screen.getByText(S.integracoes.nomes.loki).closest("li")!;
  expect(within(loki).getByText(S.integracoes.naoConfigurada)).toBeInTheDocument();
});

test("lista de incidentes mostra status e confianca e leva ao detalhe", async () => {
  mockApi({
    ...ME,
    "GET /incidents": [incident(), incident({ id: 7, status: "investigating", rca: null, alert_name: "Lento" })],
  });
  render(<App />);
  expect(await screen.findByText(/HighMemory/)).toBeInTheDocument();
  expect(screen.getByText(S.incidentes.status.investigating)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /HighMemory/ })).toHaveAttribute("href", "#/incidentes/42");
});

test("comando com quebra de linha explica por que nao ha botao de aprovar", async () => {
  const inc = incident({
    approvable: false,
    approval_blocked_reason: "controle",
    rca: { cause: "x", confidence: 0.6, evidence, suggested_command: "kubectl get pods\nkubectl get nodes", unknown: false },
  });
  mockApi({ ...ME, "GET /incidents": [inc], "GET /incidents/42": inc });
  window.location.hash = "#/incidentes/42";
  render(<App />);
  expect(await screen.findByText(/quebra de linha\/caracteres de controle não pode ser aprovado/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: S.incidente.aprovar })).not.toBeInTheDocument();
});
