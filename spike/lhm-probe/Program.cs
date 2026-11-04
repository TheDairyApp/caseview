// Spike: how much RAM/CPU does a C# process need to read CaseView's sensors?
// Mirrors the Python lean provider: CPU, GPU and motherboard fan chip only,
// temperatures/fans/loads, sensor list cached, refresh every 2 s.
using LibreHardwareMonitor.Hardware;

var lean = new HashSet<HardwareType>
{
    HardwareType.Cpu, HardwareType.GpuNvidia, HardwareType.GpuAmd, HardwareType.GpuIntel,
    HardwareType.Motherboard, HardwareType.SuperIO,
};
var kinds = new HashSet<SensorType> { SensorType.Temperature, SensorType.Fan, SensorType.Load };
int seconds = args.Length > 0 ? int.Parse(args[0]) : 0; // 0 = run until killed
bool quiet = args.Contains("--quiet");

var computer = new Computer { IsCpuEnabled = true, IsGpuEnabled = true, IsMotherboardEnabled = true };
computer.Open();

var plan = new List<(IHardware hw, List<ISensor> sensors)>();
void Visit(IHardware hw)
{
    if (lean.Contains(hw.HardwareType))
    {
        hw.Update();
        plan.Add((hw, hw.Sensors.Where(s => kinds.Contains(s.SensorType)
                                            && !s.Name.Contains("Distance to TjMax")).ToList()));
    }
    foreach (var sub in hw.SubHardware) Visit(sub);
}
foreach (var hw in computer.Hardware) Visit(hw);

float? Pick(Func<ISensor, bool> f) =>
    plan.SelectMany(p => p.sensors).FirstOrDefault(s => s.Value.HasValue && f(s))?.Value;

var start = Environment.TickCount64;
while (seconds == 0 || Environment.TickCount64 - start < seconds * 1000L)
{
    foreach (var (hw, _) in plan) hw.Update();
    var cpuTemp = Pick(s => s.Hardware.HardwareType == HardwareType.Cpu
                            && s.SensorType == SensorType.Temperature && s.Name == "CPU Package");
    var cpuFan = Pick(s => s.Hardware.HardwareType == HardwareType.SuperIO
                           && s.SensorType == SensorType.Fan && s.Name == "Fan #1");
    var gpuTemp = Pick(s => s.Hardware.HardwareType.ToString().StartsWith("Gpu")
                            && s.SensorType == SensorType.Temperature && s.Name == "GPU Core");
    if (!quiet)
        Console.WriteLine($"cpu_temp={cpuTemp?.ToString("0") ?? "-"} cpu_fan={cpuFan?.ToString("0") ?? "-"} gpu_temp={gpuTemp?.ToString("0") ?? "-"}");
    Thread.Sleep(2000);
}
computer.Close();
