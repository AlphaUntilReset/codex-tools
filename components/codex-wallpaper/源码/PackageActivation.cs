using System;
using System.Runtime.InteropServices;
using System.Text;

namespace SunFaded.Native {
    [ComImport, Guid("2E941141-7F97-4756-BA1D-9DECDE894A3D"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IApplicationActivationManager {
        [PreserveSig]
        int ActivateApplication([MarshalAs(UnmanagedType.LPWStr)] string appId,
            [MarshalAs(UnmanagedType.LPWStr)] string arguments, uint options, out uint processId);
        [PreserveSig]
        int ActivateForFile([MarshalAs(UnmanagedType.LPWStr)] string appId, IntPtr items,
            [MarshalAs(UnmanagedType.LPWStr)] string verb, out uint processId);
        [PreserveSig]
        int ActivateForProtocol([MarshalAs(UnmanagedType.LPWStr)] string appId, IntPtr items,
            out uint processId);
    }

    public static class PackageActivation {
        [DllImport("kernel32.dll", SetLastError=true)]
        static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);
        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode)]
        static extern int GetPackageFullName(IntPtr process, ref uint size, StringBuilder name);
        public static string PackageIdentity(uint pid) {
            IntPtr process = OpenProcess(0x1000, false, pid);
            if (process == IntPtr.Zero) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
            try {
                uint size=0; int code=GetPackageFullName(process, ref size, null);
                if (code == 15700) return null;
                if (code != 122) throw new System.ComponentModel.Win32Exception(code);
                var name=new StringBuilder((int)size);
                code=GetPackageFullName(process, ref size, name);
                if (code != 0) throw new System.ComponentModel.Win32Exception(code);
                return name.ToString();
            } finally { CloseHandle(process); }
        }
        [DllImport("ole32.dll", ExactSpelling=true)]
        static extern int CoCreateInstance(ref Guid classId, IntPtr outer, uint context,
            ref Guid interfaceId, [MarshalAs(UnmanagedType.Interface)] out IApplicationActivationManager manager);

        static IApplicationActivationManager Create() {
            Guid classId = new Guid("45BA127D-10A8-46EA-8AB7-56EA9078943C");
            Guid interfaceId = new Guid("2E941141-7F97-4756-BA1D-9DECDE894A3D");
            IApplicationActivationManager manager;
            // CLSCTX_LOCAL_SERVER: the shell service owns the activation lifetime.
            Marshal.ThrowExceptionForHR(CoCreateInstance(ref classId, IntPtr.Zero, 4,
                ref interfaceId, out manager));
            return manager;
        }
        public static bool Validate() {
            IApplicationActivationManager manager = Create();
            try { return manager != null; }
            finally { Marshal.ReleaseComObject(manager); }
        }
        public static uint Activate(string appId, string arguments) {
            if (String.IsNullOrWhiteSpace(appId)) throw new ArgumentException("Missing app identity.");
            IApplicationActivationManager manager = Create();
            try {
                uint processId;
                // AO_NOERRORUI prevents a startup failure from displaying a modal dialog.
                Marshal.ThrowExceptionForHR(manager.ActivateApplication(appId, arguments, 2, out processId));
                return processId;
            } finally { Marshal.ReleaseComObject(manager); }
        }
    }
}
