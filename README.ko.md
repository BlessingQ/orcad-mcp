# orcad-mcp (한국어)

AI 어시스턴트(Claude Code, Claude Desktop 등 MCP 클라이언트)가 실행 중인 **OrCAD Capture** 회로도를
읽고 고칠 수 있게 해 주는 MCP 서버입니다. [English README](README.md)

## 할 수 있는 일

| 분류 | 내용 |
|------|------|
| 읽기 | 열린 설계·페이지, 부품(Value, PCB Footprint, 모든 속성), 핀별 연결 넷, 전체 넷 연결(페이지 간 연결 포함, Capture 넷리스터와 같은 기준) |
| 수정 | 여러 부품의 속성을 한 번에 변경(Value, PCB Footprint, Part Reference, 사용자 속성). 먼저 전부 검사한 뒤 적용하고, 하나라도 실패하면 이미 바꾼 것도 되돌림 |
| 저장 | `.dsn` 저장(저장 전에 자동 백업) |
| BOM | 원하는 속성으로 묶은 BOM, CSV 저장 |
| PADS 넷리스트 | PADS Layout용 PADS PowerPCB ASCII 넷리스트(`.asc`), Capture의 PADS 포매터와 같은 형식 |
| 그리기 | 부품·선·페이지 연결 커넥터·전원/GND 심볼·접점·NC 표시 배치, 심볼 이름 변경, 영역 삭제(먼저 목록 확인), 페이지 이동 |
| 라이브러리 | Capture 없이 `.olb`에 부품 추가(사각형 몸체, `A5`·`SH1` 같은 핀 번호 가능) |
| Tcl | `run_tcl`로 Capture 데이터베이스 API(`Dbo*`)와 Capture 명령 전체 사용 |

## 동작 방식

```
MCP 클라이언트 ──stdio──> orcad-mcp (Python) ──TCP 127.0.0.1:47800──> 브리지 (Tcl, Capture 안) ──> Capture DB
```

Capture에는 데이터베이스 API가 있는 Tcl 인터프리터가 들어 있습니다. 작은 브리지 스크립트가 Capture 안에서
`127.0.0.1`로만 접속을 받아 orcad-mcp의 요청을 실행합니다. 모든 요청에는 `%USERPROFILE%\.orcad-mcp\token`에
저장된 임의 토큰이 있어야 하므로, 같은 Windows 사용자의 프로그램만 접속할 수 있습니다.

## 준비물

- Windows + **OrCAD Capture 16.5** 이상 (16.5 / Tcl 8.4.15에서 검증. 17.x도 같은 API지만 아직 시험 안 함)
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (Python은 uv가 알아서 설치):
  `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`

## 설치

### 1. Capture에 브리지 설치 (PC마다 한 번)

```powershell
uvx orcad-mcp install
```

`%USERPROFILE%\.orcad-mcp`에 브리지 스크립트와 토큰을, `<Cadence>\tools\capture\tclscripts\capAutoLoad`에
작은 로더를 씁니다(`CDSROOT` 또는 `C:\Cadence` 아래에서 찾음. 직접 지정: `--cdsroot C:\Cadence\SPB_16.5`).
이후 Capture가 시작될 때마다 브리지도 자동으로 시작됩니다.

**Capture를 다시 시작**하거나, 지금 바로 Capture의 Command Window(View > Command Window)에서 시작합니다:

```tcl
source {C:/Users/<사용자>/.orcad-mcp/orcad_bridge.tcl}
```

Command Window에 `orcad-mcp bridge 1 listening on 127.0.0.1:47800`이 나오면 됩니다. 터미널에서 확인:

```powershell
uvx orcad-mcp status
```

> 아직 PyPI에 없을 때는 `uvx orcad-mcp` 대신 GitHub 버전을 씁니다:
> `uvx --from git+https://github.com/BlessingQ/orcad-mcp orcad-mcp install`

### 2. MCP 클라이언트에 등록

**Claude Code** (이 PC의 모든 프로젝트):

```powershell
claude mcp add --scope user orcad -- uvx orcad-mcp
```

프로젝트에만 넣으려면 `--scope project`(`.mcp.json` 생성, 팀 공유) 또는 `--scope local`(나만).
등록 후 Claude Code 세션을 새로 시작합니다.

**Claude Desktop / 기타 클라이언트** (`claude_desktop_config.json` 등):

```json
{
  "mcpServers": {
    "orcad": {
      "command": "uvx",
      "args": ["orcad-mcp"]
    }
  }
}
```

클라이언트의 `PATH`에 `uvx`가 없으면 전체 경로(`C:\Users\<사용자>\.local\bin\uvx.exe`)를 씁니다.

**업데이트**: `uvx`는 패키지를 캐시합니다. 새 버전은 `uvx orcad-mcp@latest --version`(또는
`uv cache clean orcad-mcp`)으로 받습니다. 서버가 시작할 때 설치된 브리지 스크립트도 갱신하며,
`orcad_status`가 브리지 버전 불일치를 알리면 Capture를 다시 시작합니다.

## 도구

| 도구 | 기능 |
|------|------|
| `orcad_status` | 브리지 연결, Capture 버전, 열린 설계(활성 여부, 저장 안 된 변경). 연결이 안 되면 시작 방법 안내 |
| `install_bridge` | `orcad-mcp install`과 같음 (AI 클라이언트에서 실행) |
| `list_pages` | 회로도 폴더·페이지와 페이지별 부품 수 |
| `list_parts` | 부품 표(참조번호, Value, 풋프린트, 페이지, 라이브러리 부품). 와일드카드 필터, 추가 속성 열 |
| `get_part` | 모든 속성(읽기 전용 표시), 핀 이름·종류·연결 넷, 위치 |
| `list_nets` | 넷과 핀(`U1.9:PA3`). 넷 이름이나 부품으로 필터 |
| `get_bom` | 속성별로 묶은 BOM, CSV 저장 선택 |
| `set_part_properties` | 여러 부품 속성 한 번에 변경 (먼저 검사, 실패 시 전부 되돌림) |
| `save_design` | `.dsn` 저장 (먼저 `%USERPROFILE%\.orcad-mcp\backups`에 백업) |
| `open_design` / `close_design` | Capture에 열려 있지 않은 `.dsn`을 창 없이 불러오기 / 닫기 |
| `export_netlist` | PADS PowerPCB ASCII 넷리스트(`.asc`) |
| `active_page` / `goto_page` | Capture가 보여 주는 페이지 확인 / 다른 페이지로 이동(View > Next/Previous Page) |
| `page_objects` | 페이지 구성: 부품(영역, 핀 좌표), 심볼, 선. 특정 영역만 볼 수 있음 |
| `place_part` | 라이브러리 부품 배치 + 참조번호·Value·풋프린트 지정, 핀 좌표 반환 |
| `place_wire` | 점들을 잇는 선 |
| `place_symbol` | 페이지 연결 커넥터·전원·GND 심볼을 연결점 (x, y)에 배치 |
| `place_marks` | 접점(Junction) 또는 NC 표시 |
| `delete_area` | 사각형에 걸치는 것 삭제. 먼저 목록만, `apply=true`일 때 삭제 |
| `rename_symbol` | 전원/GND 심볼·페이지 연결 커넥터 이름 변경 |
| `set_no_connect` | 핀의 NC 표시 설정/해제 |
| `check_points` | 좌표가 연결점인지, 거기서 끝나는 선의 넷 |
| `create_library_parts` | Cadence Tcl로 `.olb`에 부품 추가(사본 수정 → 검증 → 백업 → 교체) |
| `run_tcl` | Capture에서 임의 Tcl 실행 (Capture를 죽이는 GUI 명령은 거부) |

`design` 인자는 `.dsn` 경로, 파일 이름, 또는 파일 이름의 일부(유일할 때)를 받습니다. 생략하면 Capture
프로젝트 매니저에서 활성인 설계를 씁니다.

요청 예:

- *"100n 커패시터 전부와 풋프린트 보여줘"* → `list_parts value="*100n*"`
- *"U2가 어떤 넷에 연결돼 있어? PA9가 정말 USART1_TX야?"* → `list_nets refdes="U2"`
- *"0603 저항 풋프린트를 전부 R1608로 바꾸고 바뀐 내용 보여줘"* → `list_parts`, `set_part_properties`
- *"Value와 Manufacturer로 묶은 BOM을 설계 폴더에 CSV로 저장해줘"* → `get_bom`
- *"PADS 넷리스트 뽑아줘"* → `export_netlist`

## 안전장치

- 읽기는 아무것도 바꾸지 않습니다.
- `set_part_properties`는 Capture 메모리의 설계를 바로 바꿉니다(활성 페이지 다시 그리기 요청). `.dsn` 파일은
  `save_design`을 해야 바뀝니다. **Capture의 Edit > Undo로는 되돌릴 수 없으므로**, 도구 결과에 이전 값이
  들어 있어 AI가 다시 설정해 되돌릴 수 있습니다.
- `save_design`은 저장 전에 현재 `.dsn`을 `%USERPROFILE%\.orcad-mcp\backups\<설계>\`에 복사합니다(최근 20개
  유지). Capture도 평소처럼 `_0.DBK` 백업을 만듭니다.
- 쓰기 도구는 MCP 어노테이션에 destructive로 표시되어 클라이언트가 승인을 묻습니다.
- `run_tcl`은 Capture Tcl이 할 수 있는 모든 일(프로그램 실행 포함)을 할 수 있습니다. `ORCAD_MCP_ALLOW_TCL=0`으로
  끌 수 있습니다.
- Capture 16.5는 스크립트의 두 가지 실수로 접근 위반이 나면서 종료됩니다(Tcl `catch`로 못 막음). NULL인
  데이터베이스 객체의 메서드 호출(예: 연결 안 된 핀의 넷 이름)과, 문서에 없는 GUI 명령(`GetWindows`,
  `GetActiveWindow`, `GetActivePage` 등) 호출입니다. 브리지가 `::NULL`을 정의해 첫 번째는 일반 Tcl 오류가 되고,
  두 번째는 `run_tcl`이 거부합니다(`allow_unsafe=true`로만 허용). 그리기 도구는 `Capture.exe` 안의
  `Declare Sub` 목록과 메뉴 리소스로 형식이 확인된 명령만 씁니다.
- 그리기 도구는 Capture가 보여 주는 페이지에 작동하며 먼저 그걸 확인합니다. `active_page`는 페이지 모서리에
  0.2 mm 표시용 선을 그었다가 지워서 페이지를 알아냅니다(페이지가 수정됨으로 표시됨).
- `delete_area`는 `apply=true`가 아니면 아무것도 지우지 않습니다. Capture의 영역 선택은 사각형에 걸치는 것을
  모두 고르고(부품 글자가 걸쳐도 포함), 미리보기 목록이 정확히 그 기준입니다.
- `create_library_parts`는 원본 라이브러리를 열지 않습니다. 사본을 고치고 다시 읽어 요청과 비교한 뒤에만
  원본을 백업하고 교체합니다. 새 부품은 Capture를 다시 시작해야 보입니다.
- 브리지는 `127.0.0.1`에서 토큰이 맞는 요청만 받습니다. 형식이 틀린 요청(예: 웹 페이지가 포트로 보낸 POST)은
  실행하지 않고 연결을 끊습니다.
- Capture는 요청을 GUI 스레드에서 실행합니다. Capture에 모달 대화상자가 열려 있으면 요청이 기다리다가
  `ORCAD_MCP_TIMEOUT`초 뒤에 시간 초과됩니다.

## 설정

| 변수 | 기본값 | 의미 |
|------|--------|------|
| `ORCAD_MCP_PORT` | `47800` | 브리지 포트 (Capture에도 같은 값 필요, 예: Windows 사용자 환경 변수) |
| `ORCAD_MCP_HOME` | `%USERPROFILE%\.orcad-mcp` | 브리지 스크립트·토큰·백업 위치 (Capture와 같아야 함) |
| `ORCAD_MCP_TIMEOUT` | `120` | Capture 응답 대기 시간(초) |
| `ORCAD_MCP_ALLOW_TCL` | `1` | `0`이면 `run_tcl` 사용 안 함 |
| `ORCAD_MCP_BACKUP` | `1` | `0`이면 `save_design` 전 백업 안 함 |
| `ORCAD_MCP_CDSROOT` | `CDSROOT` | `install`이 쓰는 Cadence 폴더 |
| `ORCAD_MCP_LOG_LEVEL` | `WARNING` | 로그 수준(stderr) |

## 명령줄

```
orcad-mcp                 MCP 서버 실행 (stdio, MCP 클라이언트가 실행하는 형태)
orcad-mcp install         브리지 설치 (위 참고), --cdsroot DIR
orcad-mcp uninstall       Capture 로더 제거, --purge는 브리지 스크립트·토큰도 삭제
orcad-mcp status          연결 확인, 열린 설계 목록
orcad-mcp tcl "<스크립트>" Capture에서 Tcl 실행 (@file.tcl 가능)
```

## 한계 / 계획

- 부품은 회전 없이(0도) 놓습니다. 넷 별칭 도구는 없습니다(페이지 연결 커넥터를 쓰세요).
- `goto_page`는 한 회로도 폴더 안에서만 움직이며, 그 설계의 페이지가 이미 하나 열려 있어야 합니다.
- 새 라이브러리 부품은 사각형 몸체와 passive 핀으로 만들어집니다.
- 재사용 블록이 있는 계층 설계(occurrence 속성): 부품을 occurrence가 아닌 인스턴스 기준으로 읽습니다.
- PDF 출력은 없습니다.
- 포트 하나당 Capture 하나.

## 개발

```powershell
git clone https://github.com/BlessingQ/orcad-mcp
cd orcad-mcp
uv sync
uv run pytest            # 단위 테스트. 브리지 프로토콜은 로컬 Tcl 인터프리터로 시험 (Capture 불필요)
uv run ruff check src tests
```

Capture 쪽 코드는 `src/orcad_mcp/tcl/`에 있습니다: `orcad_bridge.tcl`(TCP 서버, 작고 안정적으로 유지),
`orcad_mcp_lib.tcl`(조회·수정, 서버가 Capture로 보내며 내용이 바뀌면 다시 로드). Tcl 8.4 호환을 지켜야 합니다
(`{*}`, `dict`, `lassign`, `lmap`, `apply` 사용 불가).

Cadence의 Tcl API 설명서: `<Cadence>\tools\capture\tclscripts\OrCAD_Capture_TclTk_Extensions.pdf`

## 라이선스

MIT. OrCAD와 Cadence는 Cadence Design Systems, Inc.의 상표이며, 이 프로젝트는 Cadence와 관계가 없습니다.
