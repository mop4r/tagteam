// Abre o Grok no console de verdade e, na tela inicial, manda Ctrl+R uma vez.
// O grok.exe nao tem flag de lista: --resume sem id entra na ultima conversa.
// Nao copiar a saida por um pseudoconsole: o console de destino reinterpreta
// UTF-8 e as sequencias de controle, e a tela fica ilegivel.
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

public static class GrokResume
{
    const uint CREATE_SUSPENDED = 0x00000004;
    const int JobObjectExtendedLimitInformation = 9;
    const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000;
    const ushort KEY_EVENT = 1;
    const uint LEFT_CTRL_PRESSED = 0x0008;
    const int STD_INPUT_HANDLE = -10;
    const int STD_OUTPUT_HANDLE = -11;

    [StructLayout(LayoutKind.Sequential)]
    struct COORD
    {
        public short X;
        public short Y;
        public COORD(short x, short y) { X = x; Y = y; }
    }

    [StructLayout(LayoutKind.Sequential)]
    struct SMALL_RECT
    {
        public short Left, Top, Right, Bottom;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct CONSOLE_SCREEN_BUFFER_INFO
    {
        public COORD dwSize;
        public COORD dwCursorPosition;
        public short wAttributes;
        public SMALL_RECT srWindow;
        public COORD dwMaximumWindowSize;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct STARTUPINFO
    {
        public int cb;
        public IntPtr lpReserved, lpDesktop, lpTitle;
        public int dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars, dwFillAttribute, dwFlags;
        public short wShowWindow, cbReserved2;
        public IntPtr lpReserved2, hStdInput, hStdOutput, hStdError;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct PROCESS_INFORMATION
    {
        public IntPtr hProcess, hThread;
        public int dwProcessId, dwThreadId;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct JOBOBJECT_BASIC_LIMIT_INFORMATION
    {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct IO_COUNTERS
    {
        public ulong a, b, c, d, e, f;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    {
        public JOBOBJECT_BASIC_LIMIT_INFORMATION Basic;
        public IO_COUNTERS Io;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }

    // INPUT_RECORD: WORD EventType, padding, KEY_EVENT_RECORD de 16 bytes.
    [StructLayout(LayoutKind.Explicit)]
    struct INPUT_RECORD
    {
        [FieldOffset(0)] public ushort EventType;
        [FieldOffset(4)] public int bKeyDown;
        [FieldOffset(8)] public ushort wRepeatCount;
        [FieldOffset(10)] public ushort wVirtualKeyCode;
        [FieldOffset(12)] public ushort wVirtualScanCode;
        [FieldOffset(14)] public ushort UnicodeChar;
        [FieldOffset(16)] public uint dwControlKeyState;
    }

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern bool CreateProcess(string app, string cmd, IntPtr pa, IntPtr ta, bool inherit, uint flags, IntPtr env, string cwd, ref STARTUPINFO si, out PROCESS_INFORMATION pi);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern uint ResumeThread(IntPtr thread);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool CloseHandle(IntPtr h);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern uint WaitForSingleObject(IntPtr h, uint ms);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool GetExitCodeProcess(IntPtr h, out uint code);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool TerminateProcess(IntPtr h, uint code);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern IntPtr GetStdHandle(int n);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool GetConsoleScreenBufferInfo(IntPtr h, out CONSOLE_SCREEN_BUFFER_INFO info);
    [DllImport("kernel32.dll", EntryPoint = "ReadConsoleOutputCharacterW", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern bool ReadConsoleOutputCharacter(IntPtr h, [Out] char[] buf, uint n, COORD coord, out uint read);
    [DllImport("kernel32.dll", EntryPoint = "WriteConsoleInputW", SetLastError = true)]
    static extern bool WriteConsoleInput(IntPtr h, INPUT_RECORD[] rec, uint n, out uint wrote);
    [DllImport("kernel32.dll", EntryPoint = "PeekConsoleInputW", SetLastError = true)]
    static extern bool PeekConsoleInput(IntPtr h, [Out] INPUT_RECORD[] rec, uint n, out uint read);
    [DllImport("kernel32.dll", EntryPoint = "GetNumberOfConsoleInputEvents", SetLastError = true)]
    static extern bool GetNumberOfConsoleInputEvents(IntPtr h, out uint count);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern IntPtr CreateJobObject(IntPtr sa, string name);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool SetInformationJobObject(IntPtr job, int cls, IntPtr info, uint len);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool AssignProcessToJobObject(IntPtr job, IntPtr proc);
    [DllImport("kernel32.dll")]
    static extern bool SetConsoleCtrlHandler(CtrlHandler handler, bool add);

    delegate bool CtrlHandler(uint ctrl);
    static CtrlHandler ctrlHandler;
    static IntPtr childProcess = IntPtr.Zero;
    static volatile bool stop;
    static int injected;

    static int Main()
    {
        string here = Path.GetDirectoryName(typeof(GrokResume).Assembly.Location);
        string grok = Path.Combine(here, "bin", "grok.exe");
        if (grok.IndexOf('"') >= 0 || grok.IndexOf('\r') >= 0 || grok.IndexOf('\n') >= 0)
        {
            Console.Error.WriteLine("Caminho do grok.exe invalido.");
            return 1;
        }
        if (!File.Exists(grok))
        {
            Console.Error.WriteLine("Nao achei o grok.exe ao lado deste atalho.");
            return 1;
        }

        ctrlHandler = new CtrlHandler(OnCtrl);
        SetConsoleCtrlHandler(ctrlHandler, true);

        var si = new STARTUPINFO();
        si.cb = Marshal.SizeOf(typeof(STARTUPINFO));
        string cmd = "\"" + grok + "\" --always-approve";
        PROCESS_INFORMATION pi;
        // Caminho fixo, sem shell. Suspenso ate entrar no job, para nao ficar solto.
        bool ok = CreateProcess(grok, cmd, IntPtr.Zero, IntPtr.Zero, false, CREATE_SUSPENDED, IntPtr.Zero, null, ref si, out pi);
        if (!ok)
        {
            Console.Error.WriteLine("Nao consegui iniciar o Grok (" + Marshal.GetLastWin32Error() + ").");
            return 1;
        }
        childProcess = pi.hProcess;
        IntPtr job = AttachJob(pi.hProcess);
        uint resumed = ResumeThread(pi.hThread);
        CloseHandle(pi.hThread);
        if (resumed == 0xFFFFFFFF)
        {
            TerminateProcess(pi.hProcess, 1);
            if (job != IntPtr.Zero) CloseHandle(job);
            CloseHandle(pi.hProcess);
            Console.Error.WriteLine("Nao consegui soltar o Grok (" + Marshal.GetLastWin32Error() + ").");
            return 1;
        }

        var inject = new Thread(WaitAndInject);
        inject.IsBackground = true;
        inject.Start();

        WaitForSingleObject(pi.hProcess, 0xFFFFFFFF);
        stop = true;
        inject.Join(2000);
        uint code;
        int exit = 0;
        if (GetExitCodeProcess(pi.hProcess, out code) && code < 256) exit = (int)code;
        if (job != IntPtr.Zero) CloseHandle(job);
        CloseHandle(pi.hProcess);
        return exit;
    }

    static bool OnCtrl(uint ctrl)
    {
        // Ctrl+C fica com o Grok. Fechar a janela encerra este processo e o job mata o filho.
        if (ctrl == 0 || ctrl == 1) return true;
        return false;
    }

    static IntPtr AttachJob(IntPtr process)
    {
        IntPtr job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero) return IntPtr.Zero;
        var limit = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
        limit.Basic.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        int bytes = Marshal.SizeOf(typeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION));
        IntPtr mem = Marshal.AllocHGlobal(bytes);
        Marshal.StructureToPtr(limit, mem, false);
        bool set = SetInformationJobObject(job, JobObjectExtendedLimitInformation, mem, (uint)bytes);
        Marshal.FreeHGlobal(mem);
        if (!set || !AssignProcessToJobObject(job, process))
        {
            // ATALHO: sem o job, fechar a janela no meio pode deixar o Grok vivo, igual ao atalho direto; trocar quando o Windows recusar o job neste console.
            CloseHandle(job);
            return IntPtr.Zero;
        }
        return job;
    }

    static void WaitAndInject()
    {
        IntPtr stdout = GetStdHandle(STD_OUTPUT_HANDLE);
        IntPtr stdin = GetStdHandle(STD_INPUT_HANDLE);
        var relogio = System.Diagnostics.Stopwatch.StartNew();   // tempo decorrido: monotonico, nao a hora do sistema
        bool sawHint = false;
        bool readable = false;
        bool visible = false;
        while (!stop)
        {
            int elapsed = (int)relogio.ElapsedMilliseconds;
            string screen = ReadScreen(stdout);
            if (screen != null)
            {
                readable = true;
                if (HasVisible(screen)) visible = true;
                if (screen.IndexOf("ctrl+r", StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    sawHint = true;
                    break;
                }
            }
            // Sem leitura da tela: espera o tempo em que a tela inicial ja estava no ar nos testes.
            if (!readable && elapsed >= 8000) break;
            if (readable && !visible && elapsed >= 8000) break;
            if (elapsed >= 12000) break;
            Thread.Sleep(100);
        }
        if (stop) return;
        if (sawHint) Thread.Sleep(400);
        if (stop || UserPressedKey(stdin)) return;
        SendCtrlR(stdin);
    }

    static string ReadScreen(IntPtr stdout)
    {
        CONSOLE_SCREEN_BUFFER_INFO info;
        if (!GetConsoleScreenBufferInfo(stdout, out info)) return null;
        int width = info.srWindow.Right - info.srWindow.Left + 1;
        int height = info.srWindow.Bottom - info.srWindow.Top + 1;
        if (width < 1 || height < 1) return null;
        if (width > 400) width = 400;
        if (height > 120) height = 120;
        var all = new StringBuilder(width * height);
        var row = new char[width];
        for (int y = 0; y < height; y++)
        {
            uint got;
            var coord = new COORD(info.srWindow.Left, (short)(info.srWindow.Top + y));
            if (!ReadConsoleOutputCharacter(stdout, row, (uint)width, coord, out got)) return null;
            if (got > width) got = (uint)width;
            all.Append(row, 0, (int)got);
        }
        return all.ToString();
    }

    static bool HasVisible(string screen)
    {
        for (int i = 0; i < screen.Length; i++)
        {
            char ch = screen[i];
            if (ch != ' ' && ch != '\0') return true;
        }
        return false;
    }

    static bool UserPressedKey(IntPtr stdin)
    {
        uint count;
        if (!GetNumberOfConsoleInputEvents(stdin, out count) || count == 0) return false;
        if (count > 32) count = 32;
        var rec = new INPUT_RECORD[count];
        uint got;
        if (!PeekConsoleInput(stdin, rec, count, out got)) return false;
        uint n = got;
        if (n > count) n = count;
        for (uint i = 0; i < n; i++)
        {
            if (rec[i].EventType == KEY_EVENT && rec[i].bKeyDown != 0) return true;
        }
        return false;
    }

    static void SendCtrlR(IntPtr stdin)
    {
        if (Interlocked.Exchange(ref injected, 1) != 0) return;
        var rec = new INPUT_RECORD[2];
        rec[0].EventType = KEY_EVENT;
        rec[0].bKeyDown = 1;
        rec[0].wRepeatCount = 1;
        rec[0].wVirtualKeyCode = 0x52;
        rec[0].wVirtualScanCode = 0x13;
        rec[0].UnicodeChar = 0x12;
        rec[0].dwControlKeyState = LEFT_CTRL_PRESSED;
        rec[1] = rec[0];
        rec[1].bKeyDown = 0;
        uint wrote;
        if (!WriteConsoleInput(stdin, rec, 2, out wrote) || wrote != 2)
        {
            Interlocked.Exchange(ref injected, 0);
        }
    }
}
