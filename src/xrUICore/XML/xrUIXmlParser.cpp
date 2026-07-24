#include "pch.hpp"
#include "xrUIXmlParser.h"

#ifdef XRUICORE_EXPORTS
#include "ui_base.h"
#endif

#if defined(XR_PLATFORM_APPLE_IOS)
#include "xrEngine/defines.h"
#endif

namespace
{
void log_loaded_ui_xml(pcstr requested, const CUIXml& xml, bool loaded)
{
#if defined(XR_PLATFORM_APPLE_IOS)
    if (loaded && psIOSDiagnostics)
        Msg("* iOS diag: UI XML requested='%s' resolved='%s'", requested, xml.m_xml_file_name);
#else
    (void)requested;
    (void)xml;
    (void)loaded;
#endif
}
} // namespace

shared_str CUIXml::correct_file_name(pcstr path, pcstr fn)
{
#ifdef XRUICORE_EXPORTS
    if (0 == xr_strcmp(path, UI_PATH))
    {
        return UICore::get_xml_name(UI_PATH_WITH_DELIMITER, fn);
    }
    if (0 == xr_strcmp(path, UI_PATH_DEFAULT))
    {
        return UICore::get_xml_name(UI_PATH_DEFAULT_WITH_DELIMITER, fn);
    }
#endif

    return fn;
}

CUIXml::CUIXml()
{}

CUIXml::~CUIXml()
{}

bool CUIXml::Load(pcstr path_alias, pcstr xml_filename, bool fatal)
{
    const bool loaded = XMLDocument::Load(path_alias, xml_filename, fatal);
    log_loaded_ui_xml(xml_filename, *this, loaded);
    return loaded;
}

bool CUIXml::Load(pcstr path_alias, pcstr path, pcstr xml_filename, bool fatal)
{
    const bool loaded = XMLDocument::Load(path_alias, path, xml_filename, fatal);
    log_loaded_ui_xml(xml_filename, *this, loaded);
    return loaded;
}

bool CUIXml::Load(pcstr path_alias, pcstr path, pcstr path2, pcstr xml_filename, bool fatal)
{
    const bool loaded = XMLDocument::Load(path_alias, path, path2, xml_filename, fatal);
    log_loaded_ui_xml(xml_filename, *this, loaded);
    return loaded;
}
