__all__ = ["build_file_export_toolset", "build_file_toolset"]


def __getattr__(name: str):
    # Keep the package exports without importing toolsets while low-level file
    # storage is being initialized (attachments and export tools reference each
    # other through their public abstractions).
    if name == "build_file_export_toolset":
        from deep_agent.files.export_toolset import build_file_export_toolset

        return build_file_export_toolset
    if name == "build_file_toolset":
        from deep_agent.files.toolset import build_file_toolset

        return build_file_toolset
    raise AttributeError(name)
