using System;
using System.Text;
using System.IO;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
namespace SunFaded.Native {
 [ComImport,Guid("00021401-0000-0000-C000-000000000046")] class ShellLink { }
 [ComImport,Guid("000214F9-0000-0000-C000-000000000046"),InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
 interface IShellLinkW {
  void GetPath([Out,MarshalAs(UnmanagedType.LPWStr)] StringBuilder p,int c,IntPtr data,uint flags);
  void GetIDList(out IntPtr p); void SetIDList(IntPtr p);
  void GetDescription([Out,MarshalAs(UnmanagedType.LPWStr)] StringBuilder p,int c);
  void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string p);
  void GetWorkingDirectory([Out,MarshalAs(UnmanagedType.LPWStr)] StringBuilder p,int c);
  void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string p);
  void GetArguments([Out,MarshalAs(UnmanagedType.LPWStr)] StringBuilder p,int c);
  void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string p);
  void GetHotkey(out short p); void SetHotkey(short p); void GetShowCmd(out int p); void SetShowCmd(int p);
  void GetIconLocation([Out,MarshalAs(UnmanagedType.LPWStr)] StringBuilder p,int c,out int i);
  void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string p,int i);
  void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string p,uint r); void Resolve(IntPtr h,uint f);
  void SetPath([MarshalAs(UnmanagedType.LPWStr)] string p);
 }
 public static class NativeShortcut {
  [DllImport("shell32.dll",CharSet=CharSet.Unicode)] static extern int SHParseDisplayName(string name,IntPtr context,out IntPtr pidl,uint flags,out uint attributes);
  [DllImport("shell32.dll")] static extern int SHGetNameFromIDList(IntPtr pidl,uint kind,out IntPtr name);
  [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern uint GetFinalPathNameByHandle(Microsoft.Win32.SafeHandles.SafeFileHandle h,StringBuilder b,uint n,uint flags);
  public static string PhysicalFile(string file) {
   using(var f=File.Open(file,FileMode.Open,FileAccess.Read,FileShare.ReadWrite|FileShare.Delete)){
    var b=new StringBuilder(32768);if(GetFinalPathNameByHandle(f.SafeFileHandle,b,32768,0)==0)throw new System.ComponentModel.Win32Exception();
    var name=b.ToString();return name.StartsWith(@"\\?\")?name.Substring(4):name;
   }
  }
  public static string LoginCommand(string appId) {
   if(!System.Text.RegularExpressions.Regex.IsMatch(appId,@"^[A-Za-z0-9_.!\-]+$"))throw new ArgumentException("Invalid app identity");
   var command="cmd.exe /d /c cscript.exe //B //Nologo \"%USERPROFILE%\\.codex-plugins\\start.vbs\" login || explorer.exe shell:AppsFolder\\"+appId;
   if(command.Length>260)throw new ArgumentException("Login command exceeds Windows Run limit");
   return command;
  }
  public static void Create(string file,string appId,string arguments) {
   IntPtr pidl=IntPtr.Zero; object obj=new ShellLink();
   try {
    uint attributes;Marshal.ThrowExceptionForHR(SHParseDisplayName("shell:AppsFolder\\"+appId,IntPtr.Zero,out pidl,0,out attributes));
    var link=(IShellLinkW)obj;link.SetIDList(pidl);link.SetArguments(arguments);link.SetShowCmd(1);
    link.SetDescription("Codex - native registered app; wallpaper optional");((IPersistFile)obj).Save(file,true);
   } finally {if(pidl!=IntPtr.Zero)Marshal.FreeCoTaskMem(pidl);Marshal.ReleaseComObject(obj);}
  }
  public static string[] Read(string file) {
   object obj=new ShellLink();IntPtr pidl=IntPtr.Zero,name=IntPtr.Zero;
   try {
    ((IPersistFile)obj).Load(file,0);var link=(IShellLinkW)obj;
    var args=new StringBuilder(32768);link.GetArguments(args,args.Capacity);link.GetIDList(out pidl);
    if(pidl!=IntPtr.Zero)Marshal.ThrowExceptionForHR(SHGetNameFromIDList(pidl,0x80028000,out name));
    return new string[]{name==IntPtr.Zero?"":Marshal.PtrToStringUni(name),args.ToString()};
   } finally {if(name!=IntPtr.Zero)Marshal.FreeCoTaskMem(name);if(pidl!=IntPtr.Zero)Marshal.FreeCoTaskMem(pidl);Marshal.ReleaseComObject(obj);}
  }
 }
}
